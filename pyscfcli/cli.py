#!/usr/bin/env python
# Copyright 2014-2020 The PySCF Developers. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
# Author: Qiming Sun <osirpt.sun@gmail.com>
#

'''
pyscfcli parser
'''

import os
import sys
import json
import re
import warnings
import argparse
import importlib
import yaml
import ruamel.yaml
import toml
import jinja2
from collections import OrderedDict

import numpy as np
import pyscf

# Customize yaml input output
from pyscfcli import custom_yaml

_PHONY = ('args', 'kwargs', 'results')

_SCF_METHODS = (
    'HF' , 'RHF', 'ROHF', 'UHF', 'DHF',
    'KS' , 'RKS', 'ROKS', 'UKS', 'DFT',
    'KHF' , 'KRHF', 'KROHF', 'KUHF',
    'KKS' , 'KRKS', 'KORKS', 'KUKS', 'KDFT',
)

def _load_input_config(args):
    conf = args.config.read()

    if args.key:
        kwargs = dict([k.split('=') for k in args.key])
        if '{{' in conf:
            conf = jinja2.Template(conf).render(**kwargs)
        else:
            conf = conf.format(**kwargs)

    ext = os.path.splitext(args.config.name)[1]
    if ext in ('.yaml', '.yml', ''):  # '' for stdin
        if sys.version_info >= (3, 7):
            parse = ruamel.yaml.safe_load
        else:
            # FIXME:
            class OrderedLoader(yaml.Loader):
                pass
            def construct_mapping(loader, node):
                loader.flatten_mapping(node)
                return OrderedDict(loader.construct_pairs(node))
            OrderedLoader.add_constructor(
                yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                construct_mapping)
            parse = lambda conf: yaml.load(conf, OrderedLoader)
    elif ext == '.toml':
        parse = toml.loads
    elif ext == '.json':
        parse = json.loads
    elif ext == '.py':
        # FIXME
        return exec(conf)
    else:
        raise RuntimeError('Unknown input file type')
    return parse(conf)

def _assignment_statements(config, ctx=None):
    if ctx is None:
        prefix = ''
    else:
        prefix = ctx + '.'

    statements = []
    for token, val in config.items():
        if token in _PHONY:
            continue
        if isinstance(val, str):
            if '\n' in val:
                statements.append('%s%s = """%s"""' % (prefix, token, val))
            else:
                statements.append('%s%s = "%s"' % (prefix, token, val))
        else:
            statements.append('%s%s = %s' % (prefix, token, val))
    return statements

def _update_attributes(ctx, config):
    if not isinstance(config, dict):
        return ctx

    # TODO: verify that the attributes of ctx exist and the types of config
    # items match the types of attributes
    for token, val in config.items():
        if token in _PHONY:
            continue
        if isinstance(val, dict):
            # Might need to recursively update the attributes
            attr = getattr(ctx, token, None)
            if attr is None or isinstance(attr, dict):
                setattr(ctx, token, val)
            else:
                _update_attributes(attr, val)
        else:
            setattr(ctx, token, val)
    return ctx

def _make_output(result, args):
    if args.output == 'json':
        out = json.dumps(result, indent=4)
    elif args.output == 'toml':
        out = toml.dump(result)
    elif args.output == 'QCSchema':
        warnings.warn('Output format QCSchema is not supported in current version')
        out = json.dumps(result, indent=4)
    else:
        out = yaml.dump(result, default_flow_style=False)
    print(out)


class _Task(object):
    def __init__(self):
        self.config = None
        self.dry_run = False
        self._ctx = []  # keep track all created objects

    def make_header(self):
        if self.dry_run:
            print('import numpy as np')
            print('import pyscf')
            print('results = {}')

    def handle_version(self, entry_name):
        pass

    # TODO: Phase 2
    # extra modules to load than the pyscf builtin modules
    # by default import pyscf and import numpy as np
    def handle_import(self, entry_name):
        config = self.config[entry_name]
        if self.dry_run:
            statements = []
            if isinstance(config, str):
                print('import ' + config)
            else:
                for t in config:
                    print('import ' + t)
        else:
            cur_mod = sys.modules[__name__]
            if isinstance(config, str):
                importlib.import_module(config)
                root_mod = config.split('.', 1)[0]
                set(cur_mod, root_mod, importlib.import_module(root_mod))
            else:
                for t in config:
                    importlib.import_module(t)
                    root_mod = t.split('.', 1)[0]
                    set(cur_mod, root_mod, importlib.import_module(root_mod))

    def extract_results(self, entry_name, ctx):
        config = self.config[entry_name]
        if config is None or 'results' not in config:
            return

        tokens = config['results']
        if isinstance(tokens, str):
            tokens = [tokens]

        if self.dry_run:
            print('results["%s"] = {}' % entry_name)
            for token in tokens:
                print('results["%s"]["%s"] = %s.%s' % (entry_name, token, ctx, token))
        else:
            results = {}
            for token in tokens:
                cur_ctx = ctx
                for key in token.split('.'):
                    if '[' in key or '(' in key:
                        # cur_ctx = cur_ctx.key[XXX] or cur_ctx = cur_ctx.key(XXX)
                        exec('cur_ctx = cur_ctx.'+key)
                    else:
                        cur_ctx = getattr(cur_ctx, key)
                if callable(cur_ctx):
                    val = cur_ctx()
                else:
                    val = cur_ctx
                if isinstance(val, (np.ndarray, np.generic)):
                    results[token] = val.tolist()
                else:
                    results[token] = val
            config['results'] = results

    def _basic_handler(self, entry_name, ctx, instance_name=None):
        config = self.config[entry_name]
        klass = entry_name.split('-')[0]

        if self.dry_run:
            if instance_name is None:
                instance_name = klass + '_instance'
            ctx, last_ctx = instance_name, ctx
            print('%s = %s.%s()' % (ctx, last_ctx, klass))
            statements = '\n'.join(_assignment_statements(config, ctx))
            if statements:
                print(statements)
            print('%s = %s.run()' % (ctx, ctx))
        else:
            ctx, last_ctx = getattr(ctx, klass)(), ctx
            ctx = _update_attributes(ctx, config)
            ctx = ctx.run()

        self.extract_results(entry_name, ctx)
        self._ctx.append(ctx)
        return ctx

    def handle_mole_cell(self, entry_name, ctx):
        config = self.config[entry_name]
        klass = entry_name.split('-')[0]

        if self.dry_run:
            args = ',\n    '.join(_assignment_statements(config))
            if ctx is None:
                if klass == 'Mole':
                    ctx = 'mol'
                else:
                    ctx = 'cell'
                print('%s = pyscf.M(%s)' % (ctx, args))
            else:
                print('%s.build(%s)' % (ctx, args))
            self._ctx.append(ctx)
        else:
            if ctx is None:
                if 'verbose' in config:
                    ctx = pyscf.M(**config)
                else:
                    ctx = pyscf.M(**config, verbose=2)
            else:
                ctx.build(**config)
            self._ctx.append(ctx)

        self.extract_results(entry_name, ctx)
        return ctx

    def handle_scf(self, entry_name, ctx):
        config = self.config[entry_name]
        klass = entry_name.split('-')[0]

        mf_methods = ('density_fit', 'mix_density_fit', 'x2c', 'sfx2c', 'x2c1e', 'sfx2c1e', 'newton')
        config_pass1 = dict([(k, v) for k, v in config.items() if k not in mf_methods])
        config_pass2 = OrderedDict([(k, config[k]) for k in mf_methods if k in config])

        if self.dry_run:
            ctx, last_ctx = 'mf', ctx
            if last_ctx in ('mol', 'cell'):
                print('%s = %s.%s()' % (ctx, last_ctx, klass))
            else:
                print('%s = %s.mol.%s()' % (ctx, last_ctx, klass))
            print('\n'.join(_assignment_statements(config_pass1, ctx)))
            for token, val in config_pass2.items():
                args = ',\n    '.join(_assignment_statements(val))
                print('%s = %s.%s(%s)' % (ctx, ctx, token, args))
            print('%s = %s.run()' % (ctx, ctx))
        else:
            if isinstance(ctx, pyscf.gto.Mole):
                ctx, last_ctx = getattr(ctx, klass)(), ctx
            elif hasattr(ctx, 'cell'):
                ctx, last_ctx = getattr(ctx.cell, klass)(), ctx
            else:
                ctx, last_ctx = getattr(ctx.mol, klass)(), ctx

            _update_attributes(ctx, config_pass1)
            for token, val in config_pass2.items():
                ctx = getattr(ctx, token)(**val)
            # TODO: skip ctx.run if solvent model is enabled. 
            ctx = ctx.run()

        self.extract_results(entry_name, ctx)
        self._ctx.append(ctx)
        return ctx

    def handle_solvent_model(self, entry_name, ctx):
        klass = entry_name.split('-')[0]
        if klass not in ('ddCOSMO', 'ddPCM'):
            raise RuntimeError('Invalid solvent model %s' % klass)

        if self.dry_run:
            if ctx != 'mf':
                raise RuntimeError('Solvent model can be applied to SCF object only')
        else:
            if not isinstance(ctx, pyscf.scf.hf.SCF):
                raise RuntimeError('Solvent model can be applied to SCF object only')

        return self._basic_handler(entry_name, ctx, 'mf')

    def handle_mcscf(self, entry_name, ctx):
        config = self.config[entry_name]
        klass = entry_name.split('-')[0]
        mc_method, ne_no = klass.split('(')
        ne, no = re.findall('\d+', klass)

        if self.dry_run:
            if ctx not in ('mol', 'mf'):
                ctx = ctx + '._scf'

            ctx, last_ctx = 'mc', ctx
            print('%s = %s.%s(%s, %s)' % (ctx, last_ctx, mc_method, no, ne))
            print('\n'.join(_assignment_statements(config, ctx)))
            print('%s = %s.run()' % (ctx, ctx))
        else:
            if not isinstance(ctx, (pyscf.gto.mole.Mole, pyscf.scf.hf.SCF)):
                ctx = ctx._scf
            ctx, last_ctx = getattr(ctx, mc_method)(int(no), int(ne)), ctx
            ctx = _update_attributes(ctx, config)
            ctx = ctx.run()

        self.extract_results(entry_name, ctx)
        self._ctx.append(ctx)
        return ctx

    def handle_postscf(self, entry_name, ctx):
        if self.dry_run:
            if ctx not in ('mol', 'mf'):
                ctx = ctx + '._scf'
        else:
            if not isinstance(ctx, (pyscf.gto.mole.Mole, pyscf.scf.hf.SCF)):
                ctx = ctx._scf
        return self._basic_handler(entry_name, ctx, 'postscf')

    def handle_geomopt(self, entry_name, ctx):
        config = self.config[entry_name]
        if self.dry_run:
            ctx, last_ctx = ctx+'_opt', ctx
            print('%s = %s.Gradients().optimizer()' % (ctx, last_ctx))
            print('\n'.join(_assignment_statements(config, ctx)))
            print('%s = %s.run()' % (ctx, ctx))
            self.extract_results(entry_name, ctx)
            print('mol = %s.mol' % (ctx,))  # FIXME: maybe .cell for pbc
            ctx = 'mol'
        else:
            if isinstance(ctx, pyscf.grad.rhf.GradientsBasics):
                ctx, last_ctx = ctx.optimizer(), ctx
            else:
                ctx, last_ctx = ctx.Gradients().optimizer(), ctx
            ctx = _update_attributes(ctx, config)
            ctx = ctx.run()
            self.extract_results(entry_name, ctx)
            # geomopt should pass mole/cell of new geometry to the next stage
            ctx = ctx.mol  # FIXME: maybe .cell for pbc

        self._ctx.append(ctx)
        return ctx

    def handle_gradients(self, entry_name, ctx):
        return self._basic_handler(entry_name, ctx)

    def handle_custom_statements(self, entry_name, ctx):
        config = self.config[entry_name]
        klass = entry_name.split('-')[0]
        config_rest = dict([(k, v) for k, v in config.items if k not in _PHONY])

        if self.dry_run:
            kwargs = config.get('kwargs', {})
            if 'args' in config:
                args = ',\n    '.join([', '.join(config['args'])]
                                      + _assignment_statements(kwargs))
            else:
                args = ',\n    '.join(_assignment_statements(kwargs))
            print('results["%s"] = %s(%s)' % (entry_name, klass, args))
        else:
            ctx, last_ctx = sys.modules[__name__], ctx
            for key in klass.split('.'):
                if '[' in key or '(' in key:
                    exec('ctx = ctx.'+key)
                else:
                    ctx = getattr(ctx, key)
            if config_rest:
                ctx = _update_attributes(ctx, config_rest)
            if callable(ctx):
                args = config.get(args, ())
                kwargs = config.get(kwargs, {})
                results[entry_name] = ctx(*args, **kwargs)

        return last_ctx

    def run(self):
        self.make_header()

        handlers = {
            'VERSION': self.handle_version,
            'IMPORT': self.handle_import,
            'MOLE': self.handle_mole_cell,
            'CELL': self.handle_mole_cell,
            'DDCOSMO': self.handle_solvent_model,
            'DDPCM': self.handle_solvent_model,
            'SOLVENT': self.handle_solvent_model,
            'GRADIENTS': self.handle_gradients,
            'GEOMOPT': self.handle_geomopt
        }

        ctx = None
        for token, val in self.config.items():
            klass = token.split('-')[0]
            if klass.upper() in handlers:
                ctx = handlers[klass](token, ctx)
            elif klass in _SCF_METHODS:
                ctx = self.handle_scf(token, ctx)
            elif 'CAS' == klass[:3].upper():  # CASCI or CASSCF
                ctx = self.handle_mcscf(token, ctx)
            elif '.' in klass:
                ctx = self.handle_custom_statements(token, ctx)
            else:  # post-SCF
                ctx = self.handle_postscf(token, ctx)

        return ctx

    def result(self):
        self.run()
        return self.config

def main(args=None):
    parser = argparse.ArgumentParser(
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument('config', type=argparse.FileType('r'),
                        help='input config file. It can be a template.')
    parser.add_argument('-o', '--output', default='yaml', choices=['yaml', 'json', 'QCSchema'],
                        help='output format')
    parser.add_argument('-k','--key', action='append', metavar='key=value',
                        help='keys to substitute in config template.')
    parser.add_argument('--dry-run', action='store_true',
                        help='Only generate the pyscf script. Do not run the code.')

    args = parser.parse_args()
    task = _Task()
    task.config = _load_input_config(args)
    task.dry_run = args.dry_run
    result = task.result()
    if not args.dry_run:
        _make_output(result, args)

if __name__ == '__main__':
    main()
