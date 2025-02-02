libadalang_docs = {
    'libadalang.project_scenario_variable': """
        Couple name/value to define a scenario variable for a project.
    """,
    'libadalang.source_files_for_project': """
        Load the project file according to ``project_file``, ``scenario_vars``,
        ``target`` and ``runtime``. On success, return the list of source
        files in this project according to ``mode``:

        * ``default``: sources in the root project and its non-externally built
          dependencies;

        * ``root_project``: sources in the root project only;

        * ``whole_project``: sources in the whole project tree (i.e. including
          externally built dependencies);

        * ``whole_project_with_runtime``: sources in the whole project tree
          plus runtime sources.

        This raises an ``InvalidProjectError`` exception if the project cannot
        be loaded with the given arguments.
    """,
    'libadalang.create_project_unit_provider': """
        Load the project file at ``Project_File`` and return a unit provider
        that uses it.

        If ``Project`` is passed, use it to provide units, otherwise, try use
        the whole project tree.

        As unit providers must guarantee that there exists at most one source
        file for each couple (unit name, unit kind), aggregate projects that
        contains several conflicting units are not supported: trying to load
        one will yield an error (see below).

        % if lang == 'python':
        If provided, ``Scenario_Vars`` must be a dict with key strings and
        key values to describe the set of scenario variables for this
        project.

        In order to load the given project with non-default target and
        runtimes, pass these as strings to the ``target`` and ``runtime``
        arguments.

        % else:
        If not ``${null}``, ``Scenario_Vars`` must point to an array of
        ``${capi.get_name('project_scenario_variable')}`` couples to
        provide scenario variables for this project. The last element of
        this array must end with a ``{ ${null}, ${null} }`` couple.

        If not ``${null}``, ``target`` and ``runtime`` must point to valid
        NULL-terminated strings.
        % endif

        % if lang == 'c':
        When done with it, the result must be free'd with
        ``${capi.get_name('destroy_unit_provider')}``.
        % endif

        If the requested project is invalid (error while opening the file,
        error while analysing its syntax, ...), or if it is an unsupported
        aggregate project,
        % if lang == 'python':
        this raises an ``InvalidProjectError`` exception.
        % else:
        this returns ``${null}``.
        % endif
    """,
    'libadalang.project_provider.invalid_project': """
        Raised when an error occurs while loading a project file.
    """,
    'libadalang.project_provider.unsupported_view_error': """
        Raised when creating a project unit provider for an unsupported project
        view (for instance, a view with conflicting aggregated projects).
    """,
    'libadalang.create_auto_provider': """
        Return a unit provider that knows which compilation units are to be
        found in the given list of source files.

        This knowledge is built trying to parse all given input files as Ada
        source files and listing the compilation units found there. Files that
        cannot be parsed properly are discarded. If two compilation units are
        found for the same unit, the first that is found in the given input
        files is taken and the other ones are discarded.

        Source files are decoded using the given charset. If it is ``${null}``,
        the default charset (ISO-8859-1) is used.

        % if lang == 'c':
        `input_files` must point to a ``NULL``-terminated array of
        filenames.  Once this function returns, this array and the strings
        it contains can be deallocated.

        When done with it, the result must be free'd with
        ``${capi.get_name('destroy_unit_provider')}``.
        % endif

        .. admonition:: todo

            Find a way to report discarded source files/compilation units.
    """,
}
