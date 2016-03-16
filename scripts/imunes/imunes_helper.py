#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import sys
import re
import copy
import subprocess
import time
import itertools
import traceback
import shlex

from abc import ABCMeta, abstractmethod

import concurrent.futures


class Environment(object):
    """
        Stores information on execution environment.
    """

    def __init__(self, args):
        self.args = args
        self.script = args[0]
        self.file = args[1] if len(args) > 1 else None
        self.path = self.get_file_path(self.script)

    def get_file_path(self, file_name):
        pathname = os.path.dirname(file_name)
        return os.path.abspath(pathname)

    def get_dir(self, file_name):
        if file_name.find(os.sep) != -1:
            return file_name.rsplit(os.sep, 1)[0]
        return None

    def full_from_relative_path(self, file_name):
        tmp = file_name.strip()

        if tmp.startswith(os.sep):
            return tmp
        return os.path.join(self.path, tmp)

# context


class ContextEntryType(object):
    """Value type with validation"""

    _regex = None

    def __init__(self, regex=None):
        if regex:
            self._regex = re.compile(regex)

    def validate(self, key, value):
        if self._regex and not self._regex.match(value):
            raise ValueError("Invalid value {}".format(value))


class StringContextEntry(ContextEntryType):

    def __init__(self, regex=None):
        super(StringContextEntry, self).__init__(regex)

    def validate(self, key, value):
        if not isinstance(value, basestring):
            raise ValueError("String expected for {}".format(key))

        super(StringContextEntry, self).validate(key, value)


class NumberContextEntry(ContextEntryType):

    def __init__(self):
        super(NumberContextEntry, self).__init__(
            "^[\-]?[1-9][0-9]*\.?[0-9]+$")

    def validate(self, key, value):
        super(NumberContextEntry, self).validate(key, value)


class IntegerContextEntry(NumberContextEntry):
    def __init__(self, constrained=None):
        super(IntegerContextEntry, self).__init__()
        self._regex = "^[\-][1-9]+[0-9]*|[0-9]+$"
        self.constrained = constrained

    def validate(self, key, value):
        converted = None
        try:
            converted = int(value)
        except:
            raise ValueError("Integer expected for {}".format(key))

        if self.constrained is not None and converted != self.constrained:
            raise ValueError("Expected value {} for {}"
                             .format(self.constrained, key))

        super(IntegerContextEntry, self).validate(key, value)


class ArrayContextEntry(ContextEntryType):

    _value_type = None

    def __init__(self, value_type):
        super(ArrayContextEntry, self).__init__()
        self._value_type = value_type

    def validate(self, key, value):
        super(ArrayContextEntry, self).validate(key, value)

        if not isinstance(value, list):
            raise ValueError("Value must be an array")

        if self._value_type:
            for entry in value:
                if not isinstance(entry, self._value_type):
                    raise ValueError(
                        "Invalid value of type {}".format(
                            type(value).__name__))


class DictContextEntry(ContextEntryType):

    _value_type = None

    def __init__(self, value_type):
        super(DictContextEntry, self).__init__()
        self._value_type = value_type

    def validate(self, key, value):
        super(DictContextEntry, self).validate(key, value)

        if not isinstance(value, dict):
            raise ValueError("Value must be a dictionary")

        if self._value_type:
            for entry in value:
                if not isinstance(entry, self._value_type):
                    raise ValueError(
                        "Invalid value of type {}".format(
                            type(value).__name__))


class OrContextEntry(ContextEntryType):

    _entries = None

    def __init__(self, *entries):
        super(OrContextEntry, self).__init__()
        self._entries = entries

    def validate(self, key, value):

        result = False

        for arg in self._entries:
            try:
                arg.validate(key, value)
            except:
                pass
            else:
                result = True
                break

        if not result:
            raise ValueError("Invalid value of type {}".format(
                             type(value).__name__))


class AndContextEntry(ContextEntryType):

    _entries = None

    def __init__(self, *entries):
        super(AndContextEntry, self).__init__()
        self._entries = entries

    def validate(self, key, value):
        for arg in self._entries:
            arg.validate(key, value)


class AutoContextEntry(ContextEntryType):

    _type = None

    def __init__(self, value):
        self._type = type(value)

    def validate(self, key, value):
        if self._type is not type(value):
            raise ValueError("Invalid value of type {}".format(
                             type(value).__name__))


class ContextValidator(object):
    """Common context validation"""

    def __init__(self, required_entries):
        self._required_entries = required_entries

    def validate(self, context):
        if not context:
            raise ValueError("Execution context was not provided")

        required = copy.copy(self._required_entries)

        if required:
            for key, value in context.iteritems():
                if key in required:
                    del required[key]

        if required:
            raise ValueError("Missing context entries: %r" % required)

# commands


class CommandException(Exception):
    def __init__(self, message, errors=None):
        super(CommandException, self).__init__(message)
        self.errors = errors


class NodeExecException(Exception):
    def __init__(self, message, errors=None):
        super(NodeExecException, self).__init__(message)
        self.errors = errors


class ExitException(Exception):
    def __init__(self, message=None):
        super(ExitException, self).__init__(message)


class ParamConstraints(object):
    def __init__(self, min_len, constraints):
        self.min_len = min_len
        self.constraints = constraints


class ParamValidator(object):
    """Command parameters validation"""

    def __init__(self, constraints):
        self._constraints = constraints

    def validate(self, context):
        constraints = self._constraints
        cmd = context.get('cmd')

        if constraints:
            min_len = constraints.min_len
            cmd_len = len(cmd)

            if cmd_len < min_len:
                raise CommandException('Insufficient parameters')

            for key, value in constraints.constraints.iteritems():
                if key >= cmd_len:
                    raise CommandException('Invalid command param constraints')
                try:
                    value.validate(key, cmd[key])
                except Exception as e:
                    raise CommandException(e.message)


class Command(object):
    """Simulator command abstraction"""

    __metaclass__ = ABCMeta

    def __init__(self, name, desc,
                 context_constraints=None, cmd_constraints=None):

        self.name = name
        self.desc = desc.format(name=name)

        self._context_validator = ContextValidator(context_constraints)
        self._param_validator = ParamValidator(cmd_constraints)

    @abstractmethod
    def execute(self, context):
        self._context_validator.validate(context)
        self._param_validator.validate(context)


class NodeNameValidatorMixin(object):

    def _extract_nodes(self, node_str):
        if node_str == '*':
            return (True, None, None)
        elif node_str.startswith('~'):
            return (False, True, node_str[1:].split(','))
        return (False, False, node_str.split(','))

    def _valid_node(self, node, nodes, negated=False):

        result = False

        for n in nodes:
            if node.startswith(n):
                result = True
                break

        #if not result if negated else result:
        #    print "\tNode", node

        return not result if negated else result


def node_submit_command(node, commands, detached=False):

    if detached:
        args = ["himage", '-b', node]
    else:
        args = ["himage", node]

    if isinstance(commands, basestring):
        args = args + [commands]
    else:
        args.extend(commands)

    return subprocess.check_output(args)


class NodeCommand(Command, NodeNameValidatorMixin):
    """Command to execute on a target node"""

    def __init__(self, name, detached=False):

        context_required = {
            "nodes": ArrayContextEntry(StringContextEntry),
            "state": AutoContextEntry(SimulatorState.started)
        }

        cmd_required = ParamConstraints(2, {
                                        0: StringContextEntry(),
                                        1: StringContextEntry()
                                        })

        desc = """Execute a command on target node.
            {name} [node] [command]
        """

        self.detached = detached

        super(NodeCommand, self).__init__(name, desc,
                                          context_required,
                                          cmd_required)

    def execute(self, context):

        super(NodeCommand, self).execute(context)

        cmd = context.get('cmd')
        capture = context.get('capture')
        capture_data = context.get('capture_data')
        nodes = context.get('nodes')

        _any, _neg, _nodes = self._extract_nodes(cmd[0])
        node_cmd = cmd[1:]

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:

            futures = {executor.submit(node_submit_command, name, node_cmd,
                                       self.detached):
                       name for name in nodes
                       if _any or self._valid_node(name, _nodes, _neg)}

            if not futures:
                raise CommandException('Invalid node name {}'.format(node))

            for future in concurrent.futures.as_completed(futures):
                try:

                    result = future.result()
                    if capture:
                        capture_data.append(result[:-1])

                except subprocess.CalledProcessError as e:
                    print ':: Node error:', e.returncode, e.output
                    raise NodeExecException(e.message)


class NodeCaptureOutputCommand(Command):

    def __init__(self):

        desc = """Capture nodes' stdout.
            {name}
        """

        super(NodeCaptureOutputCommand, self).__init__("capture", desc)

    def execute(self, context):

        super(NodeCaptureOutputCommand, self).execute(context)

        context_update = {
            'capture': True,
            'capture_data': []
        }

        return context_update


class NodeDumpOutputCommand(Command):

    def __init__(self):

        cmd_required = ParamConstraints(1, {
                                        0: StringContextEntry()
                                        })

        desc = """Dump node's stdout since last capture command
            {name} [output_file]
        """

        super(NodeDumpOutputCommand, self).__init__("dump", desc,
                                                    None,
                                                    cmd_required)

    def execute(self, context):

        super(NodeDumpOutputCommand, self).execute(context)

        environment = context.get('environment')
        cmd = context.get('cmd')
        data = context.get('capture_data')
        target_file = environment.full_from_relative_path(cmd[0])

        try:
            with open(target_file, 'w+') as out:
                for line in data:
                    out.write(line)
        except:
            traceback.print_exc()
            raise CommandException('Cannot write to {}'.format(target_file))

        context_update = {
            'capture': False,
            'capture_data': []
        }

        return context_update


class NodeIpAddr(Command, NodeNameValidatorMixin):

    def __init__(self):

        cmd_required = ParamConstraints(2, {
                                        0: StringContextEntry(),
                                        1: StringContextEntry()
                                        })

        desc = """Export first ip addr toenv var
            {name} [node] [var_name]
        """

        super(NodeIpAddr, self).__init__("ip-addr", desc,
                                         None,
                                         cmd_required)

    def get_node_address(self, node_name):
        output = subprocess.check_output(["himage", node_name, "ifconfig"])
        addr_line_found = False
        for line in output.split("\n"):
            if addr_line_found:
                prefix = "inet addr:"
                i = line.index(prefix)
                j = i + len(prefix)
                k = line.index(" ", j)
                return line[j:k]
            # skip to the first interface other than 'lo' or 'ext0'
            if line == "" or line.startswith(" "):
                continue
            intf_name = line.split(" ", 1)[0]
            if intf_name != "lo" and intf_name != "ext0":
                addr_line_found = True

    def execute(self, context, n_calls=None):

        super(NodeIpAddr, self).execute(context)

        environment = context.get('environment')
        cmd = context.get('cmd')
        nodes = context.get('nodes')
        variables = context.get('variables')
        variable_name = cmd[1]

        _any, _neg, _nodes = self._extract_nodes(cmd[0])

        for name in nodes:
            if _any or self._valid_node(name, _nodes, _neg):
                value = self.get_node_address(name)

        variables[variable_name] = value or ''


class NodeCopyCommand(Command, NodeNameValidatorMixin):

    def __init__(self):

        context_required = {
            "nodes": ArrayContextEntry(StringContextEntry),
            "state": AutoContextEntry(SimulatorState.started)
        }

        cmd_required = ParamConstraints(3, {
                                        0: StringContextEntry(),
                                        1: StringContextEntry(),
                                        2: StringContextEntry()
                                        })

        desc = """Copy a local file.
            {name} [node] [local_file] [container_path]
        """

        super(NodeCopyCommand, self).__init__("copy", desc,
                                              context_required,
                                              cmd_required)

    def execute(self, context, n_calls=None):

        super(NodeCopyCommand, self).execute(context)

        environment = context.get('environment')
        cmd = context.get('cmd')
        nodes = context.get('nodes')

        _any, _neg, _nodes = self._extract_nodes(cmd[0])

        src_file = environment.full_from_relative_path(cmd[1])
        target_file = cmd[2]
        target_dir = environment.get_dir(target_file)
        calls = n_calls if n_calls is not None else 0

        for name in nodes:
            if _any or self._valid_node(name, _nodes, _neg):

                docker_name = subprocess.check_output(['himage', '-v',
                                                      name]).strip("\n")

                subprocess.check_output(["docker", "cp", src_file,
                                        docker_name + ":" + target_file],
                                        stderr=subprocess.STDOUT)


class SimulatorState(object):
    idle = 1
    started = 2
    stopped = 3


class SimulatorCommand(Command):
    """Simulator command abstraction"""

    __metaclass__ = ABCMeta

    def __init__(self, name, usage,
                 context_constraints=None, cmd_constraints=None):
        super(SimulatorCommand, self).__init__(name, usage,
                                               context_constraints,
                                               cmd_constraints)


class SimulatorStartCommand(SimulatorCommand):

    def __init__(self):

        context_required = {
            "state": OrContextEntry(
                AutoContextEntry(SimulatorState.started),
                AutoContextEntry(SimulatorState.idle)
            )
        }

        cmd_required = ParamConstraints(1,
                                        {0: StringContextEntry("^.*\.imn$")})

        desc = """Start IMUNES simulator with a specified network.
            {name} [network_file]
        """

        super(SimulatorStartCommand, self).__init__("start", desc,
                                                    context_required,
                                                    cmd_required)

    def execute(self, context):
        super(SimulatorStartCommand, self).execute(context)

        experiment = context.get('experiment')
        environment = context.get('environment')
        network_file = environment.full_from_relative_path(context.get('cmd')[0])

        subprocess.check_call(["imunes", "-e", experiment, "-b", network_file])

        time.sleep(2)

        output = subprocess.check_output(["himage", "-l"])

        for line in output.splitlines():
            if line.startswith(experiment):
                output = output.strip("\n )")
                _name, rest = output.split(" (", 1) if output else (None, "")
                nodes = rest.split()

        print "Nodes: ", nodes

        context_update = {
            'state': SimulatorState.started,
            'experiment': experiment,
            'network': network_file,
            'nodes': nodes
        }

        return context_update


class SimulatorStopCommand(SimulatorCommand):

    def __init__(self):

        context_required = {
            "state": IntegerContextEntry(SimulatorState.started)
        }

        desc = """Stop IMUNES.
            {name} ([experiment])
        """

        super(SimulatorStopCommand, self).__init__("stop", desc)

    def execute(self, context):

        cmd = context.get('cmd')
        sim_experiment = context.get('experiment')
        experiment = cmd[0] if cmd and len(cmd) else sim_experiment

        subprocess.check_call(["imunes", "-b", "-e",
                               experiment])

        time.sleep(2)

        if experiment is sim_experiment:

            context_update = {
                'state': SimulatorState.stopped,
                'network': None,
                'nodes': []
            }

            return context_update

        return None


class SimulatorSleepCommand(SimulatorCommand):

    def __init__(self):

        desc = """Sleep for n seconds.
            {name} [n]
        """

        cmd_required = ParamConstraints(1,
                                        {0: IntegerContextEntry()})

        super(SimulatorSleepCommand, self).__init__("sleep", desc, None,
                                                    cmd_required)

    def execute(self, context):
        secs = int(context.get('cmd')[0])
        time.sleep(secs)


class SimulatorExitCommand(SimulatorCommand):

    def __init__(self):
        super(SimulatorExitCommand, self).__init__("exit", "")

    def execute(self, context):
        raise ExitException()


class SimulatorHelpCommand(SimulatorCommand):

    def __init__(self):
        super(SimulatorHelpCommand, self).__init__("help", "([command])")

    def execute(self, context):

        commands = context.get('commands')
        cmd = context.get('cmd')

        if cmd:
            help_for = cmd[0]
            if help_for is not self.name and help_for in commands:
                print commands.get(help_for).desc
                return

        print commands.keys()


class SimulatorPrintCommand(SimulatorCommand):

    def __init__(self):
        super(SimulatorCommand, self).__init__("print", "([command])")

    def execute(self, context):

        commands = context.get('commands')
        cmd = context.get('cmd')

        if cmd:
            print ' '.join(cmd)
        else:
            print ''


class SimulatorEnvCommand(SimulatorCommand):

    def __init__(self):

        desc = """Print env data.
            {name} [env_entry]
        """

        cmd_required = ParamConstraints(1, {0: StringContextEntry()})

        super(SimulatorEnvCommand, self).__init__("env", desc,
                                                  None, cmd_required)

    def execute(self, context):
        environment = context.get('environment')
        cmd = context.get('cmd')

        if cmd:
            print context.get(cmd[0])
        else:
            print "None"


class SimulatorLocalCommand(SimulatorCommand):

    def __init__(self):

        desc = """Print env variable.
            {name} [command(s)]
        """

        cmd_required = ParamConstraints(1, {0: StringContextEntry()})

        super(SimulatorLocalCommand, self).__init__("local", desc,
                                                    None, cmd_required)

    def execute(self, context):

        cmd = context.get('cmd')
        output = subprocess.check_output(cmd)
        print output,


class SimulatorExperimentCommand(SimulatorCommand):

    def __init__(self):

        desc = """Set or get experiment name.
            {name} ([name])
        """

        super(SimulatorExperimentCommand, self).__init__("experiment",
                                                         desc)

    def execute(self, context):

        cmd = context.get('cmd')
        state = context.get('state')
        experiment = context.get('experiment')
        context_update = None

        if cmd:
            context_update = {'experiment': cmd[0]}
            return (state, context_update)

        print experiment


class SimulatorExportCommand(SimulatorCommand):

    def __init__(self):

        desc = """Set an env variable as a result of local command.
            {name} [var] [command(s)]
        """

        cmd_required = ParamConstraints(2, {0: StringContextEntry()})

        super(SimulatorExportCommand, self).__init__("export", desc,
                                                     None, cmd_required)

    def execute(self, context):

        cmd = context.get('cmd')
        var = cmd[0]
        params = cmd[1:]
        output = subprocess.check_output(params)

        variables = context.get('variables')
        # skip the newline char
        variables[var] = output[:-1] if output else ''


class Simulator(object):
    """
        Main project class. Reads commands from stdin and executes
        them on target IMUNES nodes.
    """

    experiment = "SIM"
    commands = {}
    nodes = {}

    _var_regex = re.compile("\%\{[^\}]*\}")

    def __init__(self, environment):
        self.environment = environment

        self.__add_command(SimulatorStartCommand())
        self.__add_command(SimulatorStopCommand())
        self.__add_command(SimulatorSleepCommand())
        self.__add_command(SimulatorHelpCommand())
        self.__add_command(SimulatorEnvCommand())
        self.__add_command(SimulatorLocalCommand())
        self.__add_command(SimulatorExportCommand())
        self.__add_command(SimulatorExperimentCommand())
        self.__add_command(SimulatorPrintCommand())
        self.__add_command(SimulatorExitCommand())

        self.__add_command(NodeCommand("node"))
        self.__add_command(NodeCommand("node-d", detached=True))
        self.__add_command(NodeCopyCommand())
        self.__add_command(NodeCaptureOutputCommand())
        self.__add_command(NodeDumpOutputCommand())
        self.__add_command(NodeIpAddr())

    def start(self):

        context = {
            'experiment': self.experiment,
            'nodes': self.nodes,
            'commands': self.commands,
            'environment': self.environment,
            'state': SimulatorState.idle,
            'variables': {},
            'capture': False,
            'capture_data': []
        }

        file = sys.stdin
        if self.environment.file:
            file = open(self.environment.file)

        self._start(file, context)

        file.close()

    def _start(self, file, context):
        working = True

        while working:
            line = file.readline()
            parsed = shlex.split(line)

            if not parsed or parsed[0].startswith('#'):
                continue

            name = parsed[0]
            data = parsed[1:] if len(parsed) > 1 else []

            if name == 'source':

                if not data:
                    print ':: No source file specified'
                else:
                    try:
                        with open(data[0]) as source:
                            self._start(source, context)
                    except:
                        print ':: Cannot open file', data[0]

            elif name in self.commands:

                command = self.commands.get(name)
                context['cmd'] = self._set_cmd_vars(context, data)
                result = None

                print '[dbg]', name, context['cmd']

                try:
                    result = command.execute(context)
                except ExitException:
                    working = False
                except CommandException as e:
                    print e
                    print command.desc
                except Exception as e:
                    print ':: Error executing command: {}'.format(e)
                    traceback.print_exc()
                    working = False
                else:
                    if result:
                        context.update(result)

            else:
                print ':: Unknown command: {}'.format(name)

        self._cleanup(context)

    def _cleanup(self, context):
        if context.get('state') == SimulatorState.started:
            self.commands.get('stop').execute(context)

    def __add_command(self, command):
        self.commands[command.name] = command

    def _decorate_var(self, var):
        return '%{' + var + '}'

    def _extract_var_names(self, line):
        found = self._var_regex.findall(line)
        results = []

        if found:
            for match in found:
                result = match.replace('%{', '')
                result = result.replace('}', '')

                results.append(result)

        return results

    def _set_cmd_vars(self, context, data):
        variables = context.get('variables')

        if data:
            data_copy = data[:]

            for i, item in enumerate(data_copy):
                extracted = self._extract_var_names(item)
                data_copy[i] = self._replace_vars(item,
                                                  extracted,
                                                  variables)

            return data_copy
        return None

    def _replace_vars(self, line, extracted, env_variables):
        for e in extracted:
            if e in env_variables:
                value = env_variables.get(e)
                decorated = self._decorate_var(e)
                line = line.replace(decorated, value)
        return line


def main(args):
    environment = Environment(args)
    simulator = Simulator(environment)
    simulator.start()


if __name__ == '__main__':

    if os.geteuid() != 0:
        print "This script must be run as root"
        sys.exit(1)

    main(sys.argv)
