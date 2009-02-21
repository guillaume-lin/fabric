"""
This module contains Fab's main() method plus related subroutines.

main() is executed as the command line 'fab' program and takes care of parsing options and commands, loading the user settings file, loading a fabfile, and executing the commands given.

The other callables defined in this module are internal only. Anything useful to individuals leveraging Fabric as a library, should be kept elsewhere.
"""

from optparse import OptionParser
import os
import sys

from utils import abort
from state import commands, env, win32


def rc_path():
    """
    Return platform-specific file path for $HOME/<env.settings_file>.
    """
    if not win32:
        return os.path.expanduser("~/" + env.settings_file)
    else:
        from win32com.shell.shell import SHGetSpecialFolderPath
        from win32com.shell.shellcon import CSIDL_PROFILE
        return "%s/%s" % (
            SHGetSpecialFolderPath(0,CSIDL_PROFILE),
            env.settings_file
        )


def load_settings(path):
    """
    Take given file path and return dictionary of any key=value pairs found.
    """
    if os.path.exists(path):
        comments = lambda s: s and not s.startswith("#")
        settings = filter(comments, open(path, 'r'))
        return dict((k.strip(), v.strip()) for k, _, v in
            [s.partition('=') for s in settings])


def find_fabfile():
    """
    Attempt to locate a fabfile in current or parent directories.

    Fabfiles are defined as files named 'fabfile.py' or 'Fabfile.py'. The '.py'
    extension is required, as fabfile loading (both by 'fab' and by fabfiles
    which need other sub-fabfiles) is done via importing, not exec.

    Order of search is lowercase filename, capitalized filename, in current
    working directory (where 'fab' was invoked) and then each parent directory
    in turn.

    Returns absolute path to first match, or None if no match found.
    """
    guesses = ['fabfile.py', 'Fabfile.py']
    path = '.'
    # Stop before falling off root of filesystem (should be platform agnostic)
    while os.path.split(os.path.abspath(path))[1]:
        found = filter(lambda x: os.path.exists(os.path.join(path, x)), guesses)
        if found:
            return os.path.abspath(os.path.join(path, found[0]))
        path = os.path.join('..', path)


def load_fabfile(path):
    """
    Import given fabfile path and return dictionary of its callables.
    """
    # Get directory and fabfile name
    directory, fabfile = os.path.split(path)
    # If the directory isn't in the PYTHONPATH, add it so our import will work
    added_to_path = False
    if directory not in sys.path:
        sys.path.insert(0, directory)
        added_to_path = True
    # Perform the import (trimming off the .py)
    imported = __import__(os.path.splitext(fabfile)[0])
    # Remove directory from path if we added it ourselves (just to be neat)
    if added_to_path:
        del sys.path[0]
    # Return dictionary of callables only
    return dict(filter(lambda x: callable(x[1]), vars(imported).items()))


def parse_options():
    """
    Handle command-line options with optparse.OptionParser.

    Return list of arguments, largely for use in parse_arguments().
    """
    #
    # Initialize
    #

    parser = OptionParser(usage="fab [options] <command>[:arg1,arg2=val2,host=foo,hosts='h1;h2',...] ...")

    #
    # Define options
    #

    # Version number (optparse gives you --version but we have to do it
    # ourselves to get -V too. sigh)
    parser.add_option('-V', '--version',
        action='store_true',
        dest='show_version',
        default=False,
        help="show program's version number and exit"
    )

    # List possible Fab commands
    parser.add_option('-l', '--list',
        action='store_true',
        dest='list_commands',
        default=False,
        help="print list of possible commands and exit"
    )

    # TODO: help (and argument signatures?) for a specific command
    # (or allow option-arguments to -h/--help? e.g. "fab -h foo" = help for foo)

    # TODO: verbosity selection (sets state var(s) used when printing)
    # -v / --verbose

    # TODO: specify nonstandard fabricrc file (and call load_settings() on it)
    # -f / --fabricrc ?

    # TODO: old 'let' functionality, i.e. global env additions/overrides
    # maybe "set" as the keyword? i.e. -s / --set x=y
    # allow multiple times (like with tar --exclude)

    # TODO: old 'shell' functionality. Feels kind of odd as an option, but also
    # doesn't make any sense as an actual command (since you cannot run it with
    # other commands at the same time).
    # Probably only offer long option: --shell, possibly with -S for short?

    #
    # Finalize
    #

    # Returns two-tuple, (options, args)
    return parser.parse_args()


def list_commands():
    print("Available commands:\n")
    # Want separator between name, description to be straight col
    max_len = reduce(lambda a, b: max(a, len(b)), commands.keys(), 0)
    for name, func in commands.items():
        prefix = '  ' + name.ljust(max_len)
        # Print first line of docstring
        if func.__doc__:
            lines = filter(None, func.__doc__.splitlines())
            print(prefix + '  ' + lines[0].strip())
        # Or nothing (so just the name)
        else:
            print(prefix)
    sys.exit(0)


def parse_arguments(args):
    """
    Parses the given list of arguments into command names and, optionally,
    per-command args/kwargs. Per-command args are attached to the command name
    with a colon (:), are comma-separated, and may use a=b syntax for kwargs.
    These args/kwargs are passed into the resulting command as normal Python
    args/kwargs.

    For example:

        $ fab do_stuff:a,b,c=d

    will result in the function call do_stuff(a, b, c=d).

    If 'host' or 'hosts' kwargs are given, they will be used to fill Fabric's
    host list (which is checked later on). 'hosts' will override 'host' if both
    are given.
    
    When using 'hosts' in this way, one must use semicolons (;), and must thus
    quote the host list string to prevent shell interpretation.

    For example,

        $ fab ping_servers:hosts="a;b;c",foo=bar

    will result in Fabric's host list for the 'ping_servers' command being set
    to ['a', 'b', 'c'].
    
    'host'/'hosts' are removed from the kwargs mapping at this point, so
    commands are not required to expect them. Thus, the resulting call of the
    above example would be ping_servers(foo=bar).
    """
    cmds = []
    for cmd in args:
        cmd_args = []
        cmd_kwargs = {}
        cmd_hosts = []
        if ':' in cmd:
            cmd, cmd_str_args = cmd.split(':', 1)
            for cmd_arg_kv in cmd_str_args.split(','):
                k, _, v = partition(cmd_arg_kv, '=')
                if v:
                    # Catch, interpret host/hosts kwargs
                    if k in ['host', 'hosts']:
                        if k == 'host':
                            cmd_hosts = [v.strip()]
                        elif k == 'hosts':
                            cmd_hosts = [x.strip() for x in v.split(';')]
                    # Otherwise, record as usual
                    else:
                        cmd_kwargs[k] = (v % ENV) or k
                else:
                    cmd_args.append(k)
        cmds.append((cmd, cmd_args, cmd_kwargs, cmd_hosts))
    return cmds


def main():
    try:
        try:
            # Parse command line options
            options, args = parse_options()

            # Handle version number option
            if options.show_version:
                print "Fabric " + env.version
                sys.exit(0)

            # Load settings from user settings file, into shared env dict.
            env.update(load_settings(rc_path()))

            # Find local fabfile path or abort
            fabfile = find_fabfile()
            if not fabfile:
                abort("Couldn't find any fabfiles!")

            # Load fabfile and put its commands in the shared commands dict
            commands.update(load_fabfile(fabfile))

            # Abort if no commands found
            if not commands:
                abort("Fabfile didn't contain any commands!")

            # Handle list-commands option
            if options.list_commands:
                list_commands()
            
            #
            # Import user fabfile
            #
#            # Need to add cwd to PythonPath first, though!
#            sys.path.insert(0, os.getcwd())
#            ALL_COMMANDS = load(options[0])
#            # Load Fabric builtin commands
#            # TODO: error on collision with Python keywords, builtins, or
#            ALL_COMMANDS.update(load('builtins'))
#            # Error if command list was empty
#            if not commands_to_run:
#                _fail({'fail': 'abort'}, "No commands specified!")
#            # Figure out if any specified names are invalid
#            unknown_commands = []
#            for command in commands_to_run:
#                if not command[0] in ALL_COMMANDS:
#                    unknown_commands.append(command[0])
#            # Error if any unknown commands were specified
#            if unknown_commands:
#                _fail({'fail': 'abort'}, "Command(s) not found:\n%s" % _indent(
#                    unknown_commands
#                ))
#            # At this point all commands must exist, so execute them in order.
#            for tup in commands_to_run:
#                # TODO: handle call chain
#                # TODO: handle requires
#                ALL_COMMANDS[tup[0]](*tup[1], **tup[2])
        finally:
            pass
#            _disconnect()
#        print("Done.")
    except SystemExit:
        # a number of internal functions might raise this one.
        raise
    except KeyboardInterrupt:
        print("Stopped.")
        sys.exit(1)
    except:
        sys.excepthook(*sys.exc_info())
        # we might leave stale threads if we don't explicitly exit()
        sys.exit(1)
    sys.exit(0)
