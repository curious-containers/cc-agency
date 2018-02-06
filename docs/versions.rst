Versions
--------

All Curious Containers Python package versions consists of three distict numbers separated by :code:`.` (e.g. 3.2.1).

The scheme is X.Y.Z:

* X: RED version
* Y: CC version
* Z: PACKAGE version

If you are working with a RED file in RED version 3, you must use Curious Containers packages where X is 3
(e.g. 3.2.1).

* :code:`cc-core >= 3, < 4`

If you are using :code:`cc-core == 3.2.1`, you must use other Curious Containers packages where X is 3 and Y is 2
(e.g. 3.2.2).

* :code:`cc-faice >= 3.2, < 3.3`
* :code:`cc-agency >= 3.2, < 3.3`

Use :code:`pip3 install --user --upgrade cc-faice`, which will automatically install the latest compatible version
of :code:`cc-core` as a dependency of :code:`cc-faice`.

The PACKAGE version Z is only for maintenance releases of individual packages, which do not break compatibility.
