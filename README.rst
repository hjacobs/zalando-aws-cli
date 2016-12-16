===============
Zalando AWS CLI
===============

This package provides the ``zaws`` command line utility to exchange OAuth tokens for temporary AWS credentials by calling the `AWS Credentials Service`_.

Installation
============

The ``zalando-aws-cli`` package is part of the ``stups`` bundle:

.. code-block:: bash

    $ sudo pip3 install -U stups
    $ stups configure

Usage
=====

.. code-block:: bash

    $ zaws list                  # list all allowed account roles
    $ zaws login myacc RoleName  # write ~/.aws/credentials

You can configure your default account/role to only run ``zaws``:

.. code-block:: bash

    $ zaws set-default myacc RoleName
    $ zaws

There are multiple ways of abbreviating the command line:

.. code-block:: bash

    $ zaws li            # command can be abbreviated
    $ zaws login myacc   # only the account name is needed if you have only one role
    $ zaws alias myalias myacc PowerUser # create an alias
    $ zaws lo myalias    # use the alias

Use ``-h`` to get a list of commands and help:

.. code-block:: bash

    $ zaws -h        # list top-level commands
    $ zaws login -h  # help on the "login" command

Running locally
===============

You can run the module directly during development:

.. code-block:: bash

    $ python3 -m zalando_aws_cli list
    $ python3 -m zalando_aws_cli login myacc PowerUser

Unit tests
==========

.. code-block:: bash

    $ sudo pip3 install tox
    $ tox

.. _AWS Credentials Service: https://github.com/zalando-incubator/aws-credentials-service
