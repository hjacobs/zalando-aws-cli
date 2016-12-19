import click
import configparser
import jwt
import os
import requests
import stups_cli.config
import time
import yaml
import zign.api

import zalando_aws_cli

from clickclick import Action, AliasedGroup, print_table, OutputFormat
from requests.exceptions import RequestException

CONFIG_LOCATION = 'zalando-aws-cli'

AWS_CREDENTIALS_PATH = '~/.aws/credentials'
RESOURCES = {'credentials': '/aws-accounts/{account_id}/roles/{role_name}/credentials',
             'roles':       '/aws-account-roles/{user_id}'}
MANAGED_ID_KEY = 'https://identity.zalando.com/managed-id'

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])

output_option = click.option('-o', '--output', type=click.Choice(['text', 'json', 'tsv']), default='text',
                             help='Use alternative output format')


def print_version(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo('{command} {version}'.format(command=ctx.info_name, version=zalando_aws_cli.__version__))
    ctx.exit()


@click.group(cls=AliasedGroup, invoke_without_command=True, context_settings=CONTEXT_SETTINGS)
@click.option('-V', '--version', is_flag=True, callback=print_version, expose_value=False, is_eager=True,
              help='Print the current version number and exit.')
@click.option('--awsprofile', help='Profilename in ~/.aws/credentials', default='default', show_default=True)
@click.pass_context
def cli(ctx, awsprofile):
    ctx.obj = stups_cli.config.load_config(CONFIG_LOCATION)

    if 'service_url' not in ctx.obj:
        configure_service_url()

    if not ctx.invoked_subcommand:
        ctx.invoke(login)


def get_matching_profiles(profiles: list, search_string: str) -> list:
    matches = []
    for profile in profiles:
        if profile['account_name'] == search_string\
                or profile['role_name'] == search_string or profile['account_id'] == search_string:
                matches.append(profile)
    return matches


def get_account_name_role_name(config, account_role_or_alias):
    if len(account_role_or_alias) == 0:
        if 'default' in config:
            account_name = config['default']['account_name']
            role_name = config['default']['role_name']
        else:
            raise click.UsageError('No default profile. Use "zaws set-default..." to set a default profile.')
    elif len(account_role_or_alias) == 1:
        if 'aliases' in config and account_role_or_alias[0] in config['aliases']:
            account_name = config['aliases'][account_role_or_alias[0]]['account_name']
            role_name = config['aliases'][account_role_or_alias[0]]['role_name']
        else:
            profiles = get_profiles(config['service_url'])
            matching_profiles = get_matching_profiles(profiles, account_role_or_alias[0])
            if len(matching_profiles) == 1:
                account_name = matching_profiles[0]['account_name']
                role_name = matching_profiles[0]['role_name']
            else:
                raise click.UsageError('Alias "{}" does not exist'.format(account_role_or_alias))
    else:
        account_name = account_role_or_alias[0]
        role_name = account_role_or_alias[1]
    return account_name, role_name


@cli.command()
@click.argument('account-role-or-alias', nargs=-1)
@click.option('-r', '--refresh', is_flag=True, help='Keep running and refresh access tokens automatically')
@click.option('--awsprofile', help='Profilename in ~/.aws/credentials', default='default', show_default=True)
@click.pass_obj
def login(obj, account_role_or_alias, refresh, awsprofile):
    '''Login to AWS with given account and role. An alias can also be used.

    If the user has only one role, only the AWS account is needed.'''

    account_name, role_name = get_account_name_role_name(obj, account_role_or_alias)

    repeat = True
    while repeat:
        if 'last_update' in obj and (not account_name or not role_name):
            account_name = obj['last_update']['account_name']
            role_name = obj['last_update']['role_name']

        credentials = get_aws_credentials(account_name, role_name, obj['service_url'])
        with Action('Writing temporary AWS credentials for {} {}..'.format(account_name, role_name)):
            write_aws_credentials(awsprofile, credentials['access_key_id'], credentials['secret_access_key'],
                                  credentials['session_token'])

            obj['last_update'] = {'account_name':   account_name,
                                  'role_name':      role_name,
                                  'timestamp':      time.time()}
            stups_cli.config.store_config(obj, CONFIG_LOCATION)

        if refresh:
            wait_time = 3600 * 0.9
            with Action('Waiting {} minutes before refreshing credentials..'
                        .format(round(((obj['last_update']['timestamp']+wait_time)-time.time()) / 60))) as act:
                while time.time() < obj['last_update']['timestamp'] + wait_time:
                    try:
                        time.sleep(120)
                    except KeyboardInterrupt:
                        # do not show "EXCEPTION OCCURRED" for CTRL+C
                        repeat = False
                        break
                    act.progress()
        else:
            repeat = False


@cli.command()
@click.argument('account-role-or-alias', nargs=-1)
@click.option('--awsprofile', help='Profilename in ~/.aws/credentials', default='default', show_default=True)
@click.pass_context
def require(ctx, account_role_or_alias, awsprofile):
    '''Login if necessary'''

    account_name, role_name = get_account_name_role_name(ctx.obj, account_role_or_alias)

    last_update = ctx.obj['last_update'] if 'last_update' in ctx.obj else None
    time_remaining = last_update['timestamp'] + 3600 * 0.9 - time.time() if last_update else 0

    if (time_remaining < 0 or
            (account_name and (account_name, role_name) != (last_update['account_name'], last_update['account_name']))):
        ctx.invoke(login, account_role_or_alias=account_role_or_alias, refresh=False, awsprofile=awsprofile)


@cli.command()
@output_option
@click.pass_obj
def list(obj, output):
    '''List AWS profiles'''

    profile_list = get_profiles(obj['service_url'])
    default = obj['default'] if 'default' in obj else {}

    if 'aliases' in obj:
        alias_list = {(v['account_name'], v['role_name']): alias for alias, v in obj['aliases'].items()}
    else:
        alias_list = {}

    for profile in profile_list:
        if (default and
                (profile['account_name'], profile['role_name']) == (default['account_name'], default['role_name'])):
            profile['default'] = '✓'
        else:
            profile['default'] = ''

        if (profile['account_name'], profile['role_name']) in alias_list:
            profile['alias'] = alias_list[(profile['account_name'], profile['role_name'])]
        else:
            profile['alias'] = ''

    profile_list.sort(key=lambda r: r['account_name'])

    with OutputFormat(output):
        print_table(['account_id', 'account_name', 'role_name', 'alias', 'default'], profile_list)


@cli.command()
@click.argument('alias')
@click.argument('account-name')
@click.argument('role-name')
@click.pass_obj
def alias(obj, alias, account_name, role_name):
    '''Set an alias to an account and role name.'''

    profile = get_profile(account_name, role_name, obj['service_url'])
    if not profile:
        raise click.UsageError('Profile "{} {}" does not exist'.format(account_name, role_name))

    if 'aliases' not in obj:
        obj['aliases'] = {}

    # Prevent multiple aliases for same account
    obj['aliases'] = {k: v for k, v in obj['aliases'].items()
                      if (v['account_name'], v['role_name']) != (account_name, role_name)}

    obj['aliases'][alias] = {'account_name': account_name, 'role_name': role_name}
    stups_cli.config.store_config(obj, CONFIG_LOCATION)

    click.echo('You can now get AWS credentials to {} {} with "zaws login {}".'.format(account_name, role_name, alias))


@cli.command('set-default')
@click.argument('account-name')
@click.argument('role-name')
@click.pass_obj
def set_default(obj, account_name, role_name):
    '''Set default AWS account role'''

    profile = get_profile(account_name, role_name, obj['service_url'])
    if not profile:
        raise click.UsageError('Profile "{} {}" does not exist'.format(account_name, role_name))

    obj['default'] = {'account_name': profile['account_name'], 'role_name': profile['role_name']}
    stups_cli.config.store_config(obj, CONFIG_LOCATION)

    click.echo('Default account role set to {} {}'.format(account_name, role_name))


def configure_service_url():
    '''Prompts for the Credential Service URL and writes in local configuration'''

    # Keep trying until successful connection
    while True:
        service_url = click.prompt('Enter credentials service URL')
        if not service_url.startswith('http'):
            service_url = 'https://{}'.format(service_url)
        try:
            r = requests.get(service_url + '/swagger.json', timeout=2)
            if r.status_code == 200:
                break
            else:
                click.secho('ERROR: no response from credentials service', fg='red', bold=True)
        except RequestException as e:
            click.secho('ERROR: connection error or timed out', fg='red', bold=True)

    config = stups_cli.config.load_config('zalando-aws-cli')
    config['service_url'] = service_url
    stups_cli.config.store_config(config, 'zalando-aws-cli')


def get_ztoken():
    try:
        return zign.api.get_token_implicit_flow('zaws')
    except zign.api.AuthenticationFailed as e:
        raise click.ClickException(e)


def get_aws_credentials(account_name, role_name, service_url):
    '''Requests the specified AWS Temporary Credentials from the provided Credential Service URL'''

    profile = get_profile(account_name, role_name, service_url)

    if not profile:
        raise click.UsageError('Profile "{} {}" does not exist'.format(account_name, role_name))

    credentials_url = service_url + RESOURCES['credentials'].format(account_id=profile['account_id'],
                                                                    role_name=role_name)

    token = get_ztoken()

    r = requests.get(credentials_url, headers={'Authorization': 'Bearer {}'.format(token.get('access_token'))},
                     timeout=30)
    r.raise_for_status()

    return r.json()


def get_profiles(service_url):
    '''Returns the AWS profiles for a user.

    User is implicit from ztoken'''

    token = get_ztoken()
    decoded_token = jwt.decode(token.get('access_token'), verify=False)

    if MANAGED_ID_KEY not in decoded_token:
        raise click.ClickException('Invalid token. Please check your ztoken configuration')

    roles_url = service_url + RESOURCES['roles'].format(user_id=decoded_token[MANAGED_ID_KEY])

    r = requests.get(roles_url, headers={'Authorization': 'Bearer {}'.format(token.get('access_token'))}, timeout=20)
    r.raise_for_status()

    return r.json()['account_roles']


def get_profile(account_name, role_name, service_url):
    '''Returns the profile information for the given role and account name.'''

    profiles = get_profiles(service_url)

    for item in profiles:
        if item['account_name'] == account_name and item['role_name'] == role_name:
            return item

    return None


def get_last_update(filename):
    try:
        with open(filename, 'rb') as fd:
            last_update = yaml.safe_load(fd)
    except:
        last_update = {'timestamp': 0}
    return last_update


def write_aws_credentials(profile, key_id, secret, session_token=None):
    credentials_path = os.path.expanduser(AWS_CREDENTIALS_PATH)
    os.makedirs(os.path.dirname(credentials_path), exist_ok=True)
    config = configparser.ConfigParser()
    if os.path.exists(credentials_path):
        config.read(credentials_path)

    config[profile] = {}
    config[profile]['aws_access_key_id'] = key_id
    config[profile]['aws_secret_access_key'] = secret
    if session_token:
        # apparently the different AWS SDKs either use "session_token" or "security_token", so set both
        config[profile]['aws_session_token'] = session_token
        config[profile]['aws_security_token'] = session_token

    with open(credentials_path, 'w') as fd:
        config.write(fd)


def main():
    cli()
