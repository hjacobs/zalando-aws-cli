===============
Zalando AWS CLI
===============

.. code-block:: bash

    $ zaws list                  # list all allowed account roles
    $ zaws login myacc RoleName  # write ~/.aws/credentials

Running Locally
===============

.. code-block:: bash

    $ python3 -m zalando_aws_cli list
    $ python3 -m zalando_aws_cli login myacc PowerUser
