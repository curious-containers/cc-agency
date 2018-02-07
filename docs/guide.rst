RED Guide
=========

This tutorial explains how to create a reproducible data-driven experiment and how to document it in a RED file. It is
targeted at researchers who implement or reuse software applications to execute them with certain input arguments and
files to produce outputs.


Prerequisites
-------------

This tutorial requires a Linux distribution, where nano (or another text editor), python3-pip, git and `Docker <https://www.docker.com/>`__ are
installed.

If Linux is not already installed on your computer, use `Vagrant <https://www.vagrantup.com/>`__ to create a Virtual
Machine (VM) with your preferred distribution (see `Vagrant VM Setup <#vagrant-vm-setup-optional>`__)


Vagrant VM Setup (Optional)
^^^^^^^^^^^^^^^^^^^^^^^^^^^

First install Git, Vagrant and Virtualbox.

.. code-block:: bash

   git clone https://github.com/curious-containers/vagrant-quickstart.git
   cd vagrant-quickstart
   vagrant up
   vagrant ssh


Sample Application
------------------

Lets first create our own small CLI application with Python3. It's called :code:`grepwrap`.

Create a new file and insert the Python3 code below with :code:`nano grepwrap`. Then save and close the file.

.. code-block:: python

   #!/usr/bin/env python3

   from argparse import ArgumentParser
   from subprocess import call

   OUTPUT_FILE = 'out.txt'

   parser = ArgumentParser(description='Search for query terms in text files.')
   parser.add_argument(
       'query_term', action='store', type=str, metavar='QUERY_TERM',
       help='Search for QUERY_TERM in TEXT_FILE.'
   )
   parser.add_argument(
       'text_file', action='store', type=str, metavar='TEXT_FILE',
       help='TEXT_FILE containing plain text.'
   )
   parser.add_argument(
       '-A', '--after-context', action='store', type=int, metavar='NUM',
       help='Print NUM lines of trailing context after matching lines.'
   )
   parser.add_argument(
       '-B', '--before-context', action='store', type=int, metavar='NUM',
       help='Print NUM lines of leading context before matching lines.'
   )
   args = parser.parse_args()

   command = 'grep {} {}'.format(args.query_term, args.text_file)

   if args.after_context:
       command = '{} -A {}'.format(command, args.after_context)

   if args.before_context:
       command = '{} -B {}'.format(command, args.before_context)

   command = '{} > {}'.format(command, OUTPUT_FILE)

   exit(call(command, shell=True))


Set the executable flag for :code:`grepwrap` and add the current directory to the :code:`PATH` environment variable.

.. code-block:: bash

   chmod u+x grepwrap
   export PATH=$(pwd):${PATH}


The program is a wrapper for :code:`grep`. It stores results to :code:`out.txt` and has a simplified interface. Use
:code:`grepwrap --help` to show all CLI arguments.


Create a new file with sample data by inserting the text below with :code:`nano in.txt`. Then save and close the file.

.. code-block:: text

   FOO
   BAR
   BAZ
   QUX
   QUUX


Then execute :code:`grepwrap` as follows.

.. code-block:: bash

   grepwrap -B 1 QU in.txt


Use :code:`cat out.txt` to check the programs output.

In this case the command :code:`grepwrap -B 1 QU in.txt` is an **experiment** based on the program :code:`grepwrap`,
which has a defined **CLI** and has :code:`python3` and :code:`grep` as **dependencies**. It is executed with
:code:`in.txt` as **input** file, as well as :code:`-B 1` and :code:`QU` as **input** arguments. It produces a single
file :code:`out.txt` as **output**.

The next steps of this guide, will demonstrate the formalization of the experiment, which allows for
persistent storage, enables distribution and improves reproducibility. In order to do so, we need to describe the
**CLI**, **dependencies**, **inputs** and **outputs**.


Install CC-Faice and CC-Core
----------------------------

Install the current version of :code:`cc-faice`, which will also install a compatible version of :code:`cc-core` as a
dependency.

.. code-block:: bash

   pip3 install --user cc-faice==2.0.1


* :code:`cc-core` provides the :code:`ccagent` commandline tool
* :code:`cc-faice` provides the :code:`faice` commandline tool

Both tools are located in Python's script directory, which should be included in the :code:`PATH` environment variable.
The following code prints their version numbers.

.. code-block:: bash

   ccagent --version
   faice --version


The :code:`--help` argument shows available subtools and CLIs.

.. code-block:: bash

   ccagent --help
   faice --help


If these tools cannot be found, you should modify :code:`PATH` or fall back to executing the tools as Python modules.

.. code-block:: bash

   python3 -m cc_core.agent --version
   python3 -m cc_faice --version


Please note that :code:`cc-core` and :code:`cc-faice` are compatible if the first two numbers of their versions match,
as described in the `versions documentation <versions.html>`__.


Common Workflow Language
------------------------

The `Common Workflow Language provides a syntax <http://www.commonwl.org/v1.0/CommandLineTool.html>`__ for describing a
commandline tool's CLI. Curious Containers and the RED format build upon this CLI description syntax, but only support
a subset of the CWL specification. In other words, every CWL description compatible with RED is also compatible with the
CWL standard (e.g. with `cwltool <https://github.com/common-workflow-language/cwltool>`__, a CWL reference
implementation) but not the other way round.

The supported CWL subset is specified as a jsonschema description in the :code:`cc-core` Python package. Use the
following :code:`faice` command to show the jsonschema.

.. code-block:: bash

   faice schemas show cwl

You can use :code:`faice schemas --help` and :code:`faice schemas show --help` to learn more about these subcommands.
The :code:`faice schemas list` command prints all available schemas.

Create a new file and insert the following CWL description with :code:`nano grepwrap-cli.cwl`. Then save and close the
file.

.. code-block:: yaml

   cwlVersion: "v1.0"
   class: "CommandLineTool"
   baseCommand: "grepwrap"
   doc: "Search for query terms in text files."

   inputs:
     query_term:
       type: "string"
       inputBinding:
         position: 0
       doc: "Search for QUERY_TERM in TEXT_FILE."
     text_file:
       type: "File"
       inputBinding:
         position: 1
       doc: "TEXT_FILE containing plain text."
     after_context:
       type: "int?"
       inputBinding:
         prefix: "-A"
       doc: "Print NUM lines of trailing context after matching lines."
     before_context:
       type: "int?"
       inputBinding:
         prefix: "-B"
       doc: "Print NUM lines of leading context before matching lines."

   outputs:
     out_file:
       type: "File"
       outputBinding:
         glob: "out.txt"
       doc: "Query results."


CWL uses job files to describe inputs. Create a new file and insert the following job with :code:`nano job.yml`.
Then save and close the file.


.. code-block:: yaml

   query_term: QU
   text_file:
     class: File
     path: "in.txt"
   before_context: 1


Use the :code:`ccagent cwl` subcommand to run and execute the experiment.

.. code-block:: bash

   ccagent cwl ./grepwrap-cli.cwl ./job.yml


This is equivalent to :code:`cwltool ./grepwrap-cli.cwl ./job.yml`.


RED Inputs and Outputs
----------------------

The CWL :code:`job.yml` has been used to reference input files in the local file system. To achieve reproducibility
accross different computers, which is the goal of RED and FAICE, all input files should be downloadable from remote
hosts and all output files should be uploadable to remote hosts.

Although the CWL specification also supports remote input files via the :code:`location` keyword in a job file, it
lacks the possibility to send output files to remote hosts. In addition the :code:`location` value can only be a single
string containing a URI (e.g. :code:`http://example.com`), which is a limiting factor when connecting to a non-standard
API is required (e.g. the REST API of `XNAT <https://www.xnat.org/>`__ 1.6.5 is not stateless and requires
explicit session deletion).

For the given reasons, RED extends CWL in an incompatible way, to support arbitrary **connector plugins**
written in Python. Fortunately it is possible to regain full compatibility with existing CWL implementations
by exporting a given RED experiment via FAICE (see `CWL Compatible Export <#cwl-compatible-export>`__).


Create a new file new file and insert the following RED inputs data with :code:`nano red-inputs.yml`.

.. code-block:: yaml

   query_term: QU
   text_file:
     class: File
     connector:
       pyModule: "cc_core.commons.connectors.http"
       pyClass: "Http"
       access:
         url: "https://raw.githubusercontent.com/curious-containers/vagrant-quickstart/master/in.txt"
         method: "GET"
   before_context: 1


The RED inputs format is very similar to a CWL job. Note that connectors only work with files, and that the
:code:`connector` keyword replaces :code:`path` and :code:`location`. Each connector requires the :code:`pyModule` and
:code:`pyClass` keywords to reference an importable Python class and :code:`access` for the connector's settings. The
information contained in :code:`access` is validated by the connector itself and therefore varies for different
connector implementations.

The given HTTP connector is a reference implementation and the only connector included with :code:`cc-core` (see
`RED Connectors <connectors.html>`_ for different options).

Use :code:`faice schemas show red-connector-http` to show the corresponding jsonschema and all connector options,
including BASIC or DIGEST auth.


Use the :code:`ccagent red` subcommand to run and execute the experiment.

.. code-block:: bash

   ccagent red ./grepwrap-cli.cwl ./red-inputs.yml


The RED format also requires connector descriptions for output files. Usually an external host with a static IP / domain
name and a proper Authorization configuration should be chosen for this. This improves reproducibility, because all
destinations of the original experiment results are well documented.

For the purpose of this guide, we start a local HTTP server on TCP PORT 5000 to receive the output file.

.. code-block:: bash

   # start server as background job
   faice file-server &


Create a new file new file and insert the following RED outputs data with :code:`nano red-outputs.yml`.

.. code-block:: yaml

   out_file:
     class: File
     connector:
       pyModule: "cc_core.commons.connectors.http"
       pyClass: "Http"
       access:
         url: "http://localhost:5000/server-out.txt"
         method: "POST"


Use the :code:`ccagent red` subcommand to run and execute the experiment.

.. code-block:: bash

   ccagent red ./grepwrap-cli.cwl ./red-inputs.yml -o ./red-outputs.yml


The :code:`faice file-server` is programmed to use the file name specified in the URL. Use :code:`cat server-out.txt`
to check the programs output.

You can stop the file-server as follows.

.. code-block:: bash

   # terminate the last background job
   kill %%


Container Image
---------------

The next step is to explicitely document the runtime environment with all required dependencies of :code:`grepwrap`.
Container technologies are useful to create this kind reproducible and distributable environment. For the time being,
the only container engine supported by Curious Containers is `Docker <https://www.docker.com/>`__.

Create a new file new file and insert the following Dockerfile description with :code:`nano Dockerfile`.

.. code-block:: docker

   FROM docker.io/debian:9.3-slim

   RUN apt-get update \
   && apt-get install -y python3-pip \
   && useradd -ms /bin/bash cc

   # install cc-core
   USER cc

   RUN pip3 install --no-input --user cc-core==2.0.2

   ENV PATH="/home/cc/.local/bin:${PATH}"
   ENV PYTHONPATH="/home/cc/.local/lib/python3.5/site-packages/"

   # install app
   ADD grepwrap /home/cc/.local/bin/grepwrap


As can be seen in the Dockerfile, we extend a slim Debian image from the official
`DockerHub <https://hub.docker.com/>`__ registry. To improve reproducibility, you should always add a very specific
tag like :code:`9.3-slim` or an
`image digest <https://docs.docker.com/engine/reference/commandline/images/#list-image-digests>`__.

As a first step, :code:`python3-pip` is installed from Debian repositories, then a new user :code:`cc` is created. This
is important, because :code:`faice` will always start a container with :code:`uid:gid` set to :code:`1000:1000`. This
behavior is equivalent to :code:`cwltool`. As a next step the Dockerfile switches to the :code:`cc` user, installs
:code:`cc-core==2.0.2` and explicitely sets required environment variables. Again, to ensure reproducible builds, it is
advised to specify a certain version of :code:`cc-core`. The last step is to install the application itself. In this
case the :code:`grepwrap` script is copied into the user's home directory.

Please note, that installing :code:`cc-core` is necessary for compatibility with Curious Containers. This package
provides the :code:`ccagent` script with all the functionality demonstrated in this guide.

Use the Docker client to build the image and name it :code:`grepwrap-image`.

.. code-block:: bash

   docker build --tag grepwrap-image .


Use :code:`docker image list` to check if the new image exists.

You should consider pushing the image to a registry like `DockerHub <https://hub.docker.com/>`__ and reference it by its
full URL. This ensures reproducibility across hosts. With RED it is also possible to use private Docker registries where
authorization is required. For the sake of this guide, we will only reference the image by its local name
:code:`grepwrap-image`.


FAICE
-----

TODO


CWL Compatible Export
---------------------

TODO
