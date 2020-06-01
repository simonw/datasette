.. _authentication:

================================
 Authentication and permissions
================================

Datasette's authentication system is currently under construction. Follow `issue 699 <https://github.com/simonw/datasette/issues/699>`__ to track the development of this feature.

.. _PermissionsDebugView:

Permissions Debug
=================

The debug tool at ``/-/permissions`` is only available to the root user.

It shows the thirty most recent permission checks that have been carried out by the Datasette instance.

This is designed to help administrators and plugin authors understand exactly how permission checks are being carried out, in order to effectively configure Datasette's permission system.
