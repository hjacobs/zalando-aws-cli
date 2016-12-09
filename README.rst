===============
Zalando AWS CLI
===============

.. code-block:: bash

    $ zaws list                  # list all allowed account roles
    $ zaws login myacc RoleName  # write ~/.aws/credentials

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
