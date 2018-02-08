Introduction
============

The Curious Containers (CC) project helps researchers to create, distribute and reproduce data-driven experiments. These
experiments are built around software applications packaged in (`Docker <https://www.docker.com/>`__) container images.
In this context applications are atomic entities taking files and arguments as input and producing new files as output.

The project provides a Reproducible Experiment Description (RED) format, to store an experiment in a single file. RED
extends the `CommandLineTool description format <http://www.commonwl.org/v1.0/CommandLineTool.html>`__ of the well-known
`Common Workflow Language <http://www.commonwl.org/>`__ (CWL). Although RED is an extension to CWL, it remains full CWL
compatibility via an automatic export tool in the FAICE toolsuite.

Learn more in the comprehensive `RED Guide <guide.html>`__.
