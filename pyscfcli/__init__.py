'''
* Purpose:
  1. Provide a simple template input, that defines a unique quantum chemistry
    calculation. The input allows one to customize a calculation completely from
    cli. A very basic rule: it cannot be more complicated than writing a python
    script from scratch.
  2. Can be parsed by other programs or tools.
  3. Not necessary to cover all functionalities of pyscf.
  4. The parser works like a compiler, generate a Python script.
  5. A straight-forward config make users know how to manipulate the pyscf
    calculation, without knowing the underlying modules

* Yaml, Json (QCSchema in the future) format as input and output

* Input sample

version: v1
import (optionally, phase 2):
  extra modules to load than the pyscf builtin modules
  by default import pyscf and import numpy as np
Mole-or-Cell:
  verbose: 4
  atom: |
    H 0 0 0
    H 0 0 1
  basis:
    name-string-or-anything-supported-by-basis-input
  output:
    output-filename
  results: (a list of attributes in results, if key-value are provided, the
            key will be used as function name, values are used as arguments
            (or kwargs))
    atom_coords
HF-or-any-method-keywords:
  conv_tol: 1e-9
  level_shift: 0.2
  max_cycle: 12
  xc:
    b3lyp
  grids:
    level: 3
  density_fit:
    auxbasis: weigend
  newton:
    micro: 2
  x2c:
    atom
  results:
    - mo_energy
    - e_tot
Solvent-model:
  eps: 1.8
Post-HF-methods-or-works:
  results:
    - e_tot
    - e_corr
Gradients-or-other-properties:
  unit: au
Geomopt:


* cli arguments
  pyscfcli -c input.yaml -k key=val
'''

