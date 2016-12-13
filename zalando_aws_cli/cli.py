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
from zign.api import AuthenticationFailed

AWS_CREDENTIALS_PATH = '~/.aws/credentials'
CONFIG_DIR_PATH = click.get_app_dir('zalando-aws-cli')
CONFIG_FILE_PATH = os.path.join(CONFIG_DIR_PATH, 'zalando-aws-cli.yaml')

CONTEXT_SETTINGS = dict(help_option_names=['-h', '--help'])

CREDENTIALS_RESOURCE = '/aws-accounts/{account_id}/roles/{role_name}/credentials'
ROLES_RESOURCE = '/aws-account-roles/{user_id}'

MANAGED_ID_KEY = 'https://identity.zalando.com/managed-id'

def print_version(ctx, param, value):
    if not value or ctx.resilient_parsing:
        return
    click.echo('Zalando AWS CLI {}'.format(zalando_aws_cli.__version__))
    ctx.exit()


output_option = click.option('-o', '--output', type=click.Choice(['text', 'json', 'tsv']), default='text',
                             help='Use alternative output format')


@click.group(cls=AliasedGroup, invoke_without_command=True, context_settings=CONTEXT_SETTINGS)
@click.option('--config-file', '-c', help='Use alternative configuration file',
              default=CONFIG_FILE_PATH, metavar='PATH')
@click.option('-V', '--version', is_flag=True, callback=print_version, expose_value=False, is_eager=True,
              help='Print the current version number and exit.')
@click.option('--awsprofile', help='Profilename in ~/.aws/credentials', default='default', show_default=True)
@click.pass_context
def cli(ctx, config_file, awsprofile):
    path = os.path.abspath(os.path.expanduser(config_file))
    data = {}
    if os.path.exists(path):
        with open(path, 'rb') as fd:
            data = yaml.safe_load(fd)

    ctx.obj = {'config': data,
               'config-file': path,
               'config-dir': os.path.dirname(path),
               'last-update-filename': os.path.join(os.path.dirname(path), 'last_update.yaml')}

    if 'service_url' not in data:
        write_service_url(data, path)

    if not ctx.invoked_subcommand:
        account, role = None, None
        if 'default_account' in data:
            account = data['default_account']
            role = data['default_role']

        if not account:
            raise click.UsageError('No default profile configured. Use "zaws set-default..." to set a default profile.')
        ctx.invoke(login, account=account, role=role)


@cli.command()
@click.argument('account-name')
@click.argument('role-name')
@click.option('-r', '--refresh', is_flag=True, help='Keep running and refresh access tokens automatically')
@click.option('--awsprofile', help='Profilename in ~/.aws/credentials', default='default', show_default=True)
@click.pass_obj
def login(obj, account_name, role_name, refresh, awsprofile):
    '''Login to AWS with given account and role'''

    repeat = True
    while repeat:
        last_update = get_last_update(obj)
        if 'account_name' in last_update and last_update['account_name'] and (not account_name or not role_name):
            account_name, role_name = last_update['account_name'], last_update['role_name']

        credentials = get_aws_credentials(account_name, role_name, obj['config']['service_url'])
        with Action('Writing temporary AWS credentials for {} {}..'.format(account_name, role_name)):
            write_aws_credentials(awsprofile, credentials['access_key_id'], credentials['secret_access_key'],
                                  credentials['session_token'])
            with open(obj['last-update-filename'], 'w') as fd:
                yaml.safe_dump({'timestamp': time.time(), 'account_name': account_name, 'role_name': role_name}, fd)

        if refresh:
            last_update = get_last_update(obj['last-update-filename'])
            wait_time = 3600 * 0.9
            with Action('Waiting {} minutes before refreshing credentials..'
                        .format(round(((last_update['timestamp']+wait_time)-time.time()) / 60))) as act:
                while time.time() < last_update['timestamp'] + wait_time:
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
@output_option
@click.pass_obj
def list(obj, output):
    '''List AWS profiles'''

    service_url = obj['config']['service_url']
    role_list = get_profiles(service_url)

    with OutputFormat(output):
        print_table(sorted(role_list[0].keys()), role_list)


@cli.command('set-default')
@click.argument('account')
@click.argument('role')
@click.pass_obj
def set_default(obj, account, role):
    '''Set default AWS account and role'''

    role_list = get_profiles(obj['user'])

    if (account, role) not in [ (item['name'], item['role']) for item in role_list ]:
         raise click.UsageError('Profile "{} {}" does not exist'.format(account, role))

    obj['config']['default_account'] = account
    obj['config']['default_role'] = role

    with Action('Storing configuration in {}..'.format(obj['config-file'])):
        os.makedirs(obj['config-dir'], exist_ok=True)
        with open(obj['config-file'], 'w') as fd:
            yaml.safe_dump(obj['config'], fd)


def write_service_url(data, path):
    '''Prompts for the Credential Service URL and writes in local configuration'''

    # Keep trying until successful connection
    while True:
        service_url = click.prompt('Enter credentials service URL')
        if not service_url.startswith('http'):
            service_url = 'https://{}'.format(service_url)
        try:
            r = requests.get(service_url + '/swagger.json')
            if r.status_code == 200:
               break
            else:
               click.secho('ERROR: no response from credentials service', fg='red', bold=True)
        except RequestException as e:
            click.secho('ERROR: connection error or timed out', fg='red', bold=True)

    data['service_url'] = service_url

    with Action('Storing new credentials service URL in {}..'.format(path)):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as fd:
            yaml.safe_dump(data, fd)


def get_ztoken():
    try:
        return zign.api.get_token_implicit_flow('zaws')
    except zign.api.AuthenticationFailed as e:
        raise click.ClickException(e)


def get_aws_credentials(account_name, role_name, service_url):
    '''Requests the specified AWS Temporary Credentials from the provided Credential Service URL'''

    profiles = get_profiles(service_url)

    account_id = None
    for item in profiles:
        if item['account_name'] == account_name and item['role_name'] == role_name:
            account_id = item['account_id']

    if not account_id:
        raise click.UsageError('Profile "{} {}" does not exist'.format(account_name, role_name))

    credentials_url = service_url + CREDENTIALS_RESOURCE.format(account_id=account_id, role_name=role_name)

    token = get_ztoken()

    r = requests.get(credentials_url, headers={'Authorization': 'Bearer {}'.format(token.get('access_token'))})
    r.raise_for_status()

    return r.json()

def get_profiles(service_url):
    '''Returns the AWS profiles for a user.

    User is implicit form ztoken'''

    token = get_ztoken()
    decoded_token = jwt.decode(token.get('access_token'), verify=False)

    if MANAGED_ID_KEY not in decoded_token:
        raise click.ClickException('Invalid token. Please check your ztoken configuration')

    roles_url = service_url + ROLES_RESOURCE.format(user_id=decoded_token[MANAGED_ID_KEY])

    r = requests.get(roles_url, headers={'Authorization': 'Bearer {}'.format(token.get('access_token'))})
    r.raise_for_status()

    return r.json()['account_roles']


def get_last_update(filename):
    try:
        with open(filename, 'rb') as fd:
            last_update = yaml.safe_load(fd)
    except:
        last_update = {'timestamp': 0}
    return last_update


@cli.command()
@click.argument('profile', nargs=-1)
@click.option('--awsprofile', help='Profilename in ~/.aws/credentials', default='default', show_default=True)
@click.pass_context
def require(context, profile, awsprofile):
    '''Login if necessary'''

    last_update = get_last_update(context.obj)
    time_remaining = last_update['timestamp'] + 3600 * 0.9 - time.time()
    if time_remaining < 0 or (profile and profile[0] != last_update['profile']):
        context.invoke(login, profile=profile, refresh=False, awsprofile=awsprofile)


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
