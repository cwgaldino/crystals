# -*- coding: utf-8 -*-
"""
This package allows for manipulation and modelling of atomic structures in crystalline form.
"""
__author__ = "Laurent P. René de Cotret"
__email__ = "laurent.renedecotret@mail.mcgill.ca"
__license__ = "BSD3"
__version__ = "0.6.5"

from .atom import Atom
from .atom import Element
from .atom import frac_coords
from .atom import is_element
from .atom import real_coords
from .atom import distance_fractional
from .atom import distance_cartesian
from .atom_data import ELEM_TO_MAGMOM
from .atom_data import ELEM_TO_MASS
from .atom_data import ELEM_TO_NAME
from .atom_data import ELEM_TO_NUM
from .atom_data import NUM_TO_ELEM
from .base import AtomicStructure
from .base import Base
from .biological import Residue
from .biological import Helix
from .biological import Sheet
from .conversion import ase_atoms
from .conversion import write_xyz
from .crystal import Crystal
from .crystal import Supercell
from .crystal import CenteringType
from .crystal import symmetry_expansion
from .lattice import Lattice
from .lattice import LatticeSystem
from .lattice import lattice_system
from .parsers import CIFParser
from .parsers import CODParser
from .parsers import ParseError
from .parsers import PDBParser
from .parsers import PWSCFParser
