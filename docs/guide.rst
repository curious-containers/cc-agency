Quickstart Guide
================

This tutorial explains how to create a reproducible data-driven experiment and how to document it in a RED file. It is
targeted at researchers who implement or reuse software applications to execute them with certain input arguments and
files to produce outputs.


Prerequisites
-------------

* Linux OS

   * if Linux is not already installed on your computer, use `Vagrant <https://www.vagrantup.com/>`__ to create a Virtual Machine (VM) with your preferred distribution (see `Vagrant VM Setup <#vagrant-vm-setup-optional>`__)

* bash, python3-pip, git and `Docker <https://www.docker.com/>`__ are installed


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

   parser = ArgumentParser(description='Search for query terms in a text file.')
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

   chmod +x grepwrap
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

The next steps of this quickstart guide, will demonstrate the formalization of the experiment, which allows for
persistent storage, enables distribution and improves reproducibility. In order to do so, we need to describe the
**CLI**, **dependencies**, **inputs** and **outputs**.

Common Workflow Language
------------------------


