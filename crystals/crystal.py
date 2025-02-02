# -*- coding: utf-8 -*-
from collections import namedtuple
from enum import Enum, unique
from functools import lru_cache
from glob import glob
from itertools import islice, product
from pathlib import Path

import numpy as np
from spglib import (
    find_primitive,
    get_error_message,
    get_spacegroup_type,
    get_symmetry,
    get_symmetry_dataset,
    refine_cell,
)

from .affine import affine_map, change_of_basis
from .atom import Atom
from .base import AtomicStructure
from .lattice import Lattice
from .parsers import CIFParser, CODParser, PDBParser, PWSCFParser
from .spg_data import Hall2HM

CIF_ENTRIES = frozenset((Path(__file__).parent / "cifs").glob("*.cif"))


is_atom = lambda a: isinstance(a, Atom)
is_structure = lambda s: isinstance(s, AtomicStructure)


SymmetryOperation = namedtuple("SymmetryOperation", ("rotation", "translation"))


@unique
class CenteringType(Enum):
    """
    Enumeration of possible centering types. Together with the lattice system,
    these centering types defined all 14 Bravais lattices in 3D.

    The possible centering types are:

    * ``'P'`` : Primitive

    * ``'I'`` : Body-centered

    * ``'F'`` : Face-centered

    * ``'C'`` : Base-centered

    * ``'R'`` : Rhombohedral in hexagonal setting.
    """

    primitive = "P"
    base_centered = "C"
    body_centered = "I"
    face_centered = "F"
    rhombohedral = "R"


def symmetry_expansion(atoms, symmetry_operators):
    """
    Generate a set of unique atoms and structures from an asymmetric cell 
    and symmetry operators.

    Parameters
    ----------
    atoms : iterable of ``Atom`` or ``AtomicStructures``
        Assymetric unit cell atoms. It is assumed that the atomic 
        coordinates are in fractional form. Transformations work
        the same way for ``Atom`` objects and ``AtomicStructures``
        objects: a copy is made and moved to the symmetric location.
    symmetry_operators : iterable of array_like
        Symmetry operators that generate the full unit cell.
    
    Yields
    ------
    it : ``Atom`` or ``AtomicStructures``
        Appropriately-transformed object. Original objects are left untouched.
    """
    # TODO: provide ability to reduce to primitive, niggli_reduce, etc.
    #       using spglib?
    symmetry_operators = tuple(map(affine_map, symmetry_operators))

    unique_atoms = set([])
    for atm in filter(is_atom, atoms):
        for sym_op in symmetry_operators:
            new = atm.transform(sym_op)
            new.coords_fractional[:] = np.mod(new.coords_fractional, 1)
            unique_atoms.add(new)

    unique_structures = set([])
    for structure in filter(is_structure, atoms):
        for sym_op in symmetry_operators:
            new = structure.transform(sym_op)
            unique_structures.add(new)

    yield from unique_atoms
    yield from unique_structures


class Crystal(AtomicStructure, Lattice):
    """
    The :class:`Crystal` class is a set-like container that represent 
    crystalline structures. In addition to constructing the ``Crystal`` 
    object yourself, other constructors are also available 
    (and preferred):
    
    * ``Crystal.from_cif``: create an instance from a CIF file;
    
    * ``Crystal.from_pdb``: create an instance from a Protein Data Bank entry;
    
    * ``Crystal.from_database``: create an instance from the internal database of CIF files;
    
    * ``Crystal.from_cod``: create an instance from a Crystallography Open Database entry.

    * ``Crystal.from_pwscf``: create an instance from the output of the PWSCF program.

    * ``Crystal.from_ase``: create an instance from an ``ase.Atoms`` instance.

    Parameters
    ----------
    unitcell : iterable of ``Atom`` or ``AtomicStructures``
        Unit cell atoms or substructures. It is assumed that the atoms are 
        in fractional coordinates. 
    lattice_vectors : iterable of array_like
        Lattice vectors. If ``lattice_vectors`` is provided as a 3x3 array, it 
        is assumed that each lattice vector is a row.
    source : str or None, optional
        Provenance, e.g. filename. Only used for bookkeeping.
    """

    builtins = frozenset(map(lambda fn: fn.stem, CIF_ENTRIES))

    def __init__(self, unitcell, lattice_vectors, source=None, **kwargs):
        unitcell = list(unitcell)
        super().__init__(
            atoms=filter(is_atom, unitcell),
            substructures=filter(is_structure, unitcell),
            lattice_vectors=lattice_vectors,
            **kwargs,
        )

        for atom in super().__iter__():
            atom.lattice = Lattice(self.lattice_vectors)

        self.source = source

    @property
    def unitcell(self):
        """ Generator of atoms forming the crystal unit cell. """
        return self.__iter__()

    @classmethod
    @lru_cache(maxsize=len(builtins), typed=True)  # saves a lot of time in tests
    def from_cif(cls, path, **kwargs):
        """
        Returns a Crystal object created from a CIF 1.0, 1.1 or 2.0 file.
        Keyword arguments are passed to the Crystal constructor.

        Parameters
        ----------
        path : path-like
            File path
        """
        with CIFParser(filename=path) as parser:
            return cls(
                unitcell=symmetry_expansion(
                    parser.atoms(), parser.symmetry_operators()
                ),
                lattice_vectors=parser.lattice_vectors(),
                source=str(path),
                **kwargs,
            )

    @classmethod
    def from_database(cls, name, **kwargs):
        """ 
        Returns a Crystal object create from the internal CIF database.
        Keyword arguments are passed to the class constructor.

        Parameters
        ----------
        name : path-like
            Name of the database entry. Available items can be retrieved from `Crystal.builtins`
        """
        if name not in cls.builtins:
            raise ValueError(
                "Entry {} is not available in the database. See `Crystal.builtins` for valid entries.".format(
                    name
                )
            )

        path = Path(__file__).parent / "cifs" / (name + ".cif")
        return cls.from_cif(path, **kwargs)

    @classmethod
    def from_cod(cls, num, revision=None, download_dir=None, overwrite=False, **kwargs):
        """ 
        Returns a Crystal object built from the Crystallography Open Database. 
        Keyword arguments are passed to the class constructor.

        Parameters
        ----------
        num : int
            COD identification number.
        revision : int or None, optional
            Revision number. If None (default), the latest revision is used.
        download_dir : path-like object, optional
            Directory where to save the CIF file. Default is a local folder in the current directory
        overwrite : bool, optional
            Whether or not to overwrite files in cache if they exist. If no revision 
            number is provided, files will always be overwritten. 
        """
        with CODParser(num, revision, download_dir, overwrite) as parser:
            return cls(
                unitcell=symmetry_expansion(
                    parser.atoms(), parser.symmetry_operators()
                ),
                lattice_vectors=parser.lattice_vectors(),
                source="COD num:{n} rev:{r}".format(n=num, r=revision),
                **kwargs,
            )

    @classmethod
    def from_pdb(cls, ID, download_dir=None, overwrite=False, **kwargs):
        """
        Returns a Crystal object created from a Protein DataBank entry.
        Keyword arguments are passed to the class constructor.

        Parameters
        ----------
        ID : str
            Protein DataBank identification. The correct .pdb file will be downloaded,
            cached and parsed.
        download_dir : path-like object, optional
            Directory where to save the PDB file.
        overwrite : bool, optional
            Whether or not to overwrite files in cache if they exist. If no revision 
            number is provided, files will always be overwritten. 
        """
        with PDBParser(ID=ID, download_dir=download_dir) as parser:
            return cls(
                unitcell=symmetry_expansion(
                    parser.residues(), parser.symmetry_operators()
                ),
                lattice_vectors=parser.lattice_vectors(),
                source=parser.filename,
                **kwargs,
            )

    @classmethod
    def from_pwscf(cls, path, **kwargs):
        """
        Returns a Crystal object created from an output file of PWSCF.
        Keyword arguments are passed to the class constructor.

        Parameters
        ----------
        path : path-like
            File path
        """
        with PWSCFParser(path) as parser:
            return cls(
                unitcell=parser.atoms(),
                lattice_vectors=parser.lattice_vectors(),
                source=parser.filename,
                **kwargs,
            )

    @classmethod
    def from_ase(cls, atoms, **kwargs):
        """
        Returns a Crystal object created from an ASE Atoms object.
        Keyword arguments are passed to the class constructor.
        
        Parameters
        ----------
        atoms : ase.Atoms
            Atoms group.
        """
        lattice_vectors = atoms.get_cell()

        return cls(
            unitcell=[Atom.from_ase(atm) for atm in atoms],
            lattice_vectors=lattice_vectors,
            **kwargs,
        )

    def _spglib_cell(self):
        """ Returns an array in spglib's cell format. """
        # To get symmetry information, we only give spglib the unit cell atoms
        # This way, all spglib-related methods (like symmetry()) will act on the unit cell only.
        # This distinction is important for Crystal subclasses, like Supercell.
        unitcell = np.stack([np.asarray(atm) for atm in self.unitcell])
        return np.array(self.lattice_vectors), unitcell[:, 1:], unitcell[:, 0]

    def primitive(self, symprec=1e-2):
        """ 
        Returns a Crystal object in the primitive unit cell.
        
        Parameters
        ----------
        symprec : float, optional
            Symmetry-search distance tolerance in Cartesian coordinates [Angstroms].

        Returns
        -------
        primitive : Crystal
            Crystal with primitive cell. Even if the crystal already has a primitive
            cell, a new crystal is returned.

        Raises
        ------
        RuntimeError : If primitive cell could not be found.
        
        Notes
        -----
        Optional atomic properties (e.g magnetic moment) might be lost in the reduction.
        """
        search = find_primitive(self._spglib_cell(), symprec=symprec)
        if search is None:
            raise RuntimeError("Primitive cell could not be found.")

        lattice_vectors, scaled_positions, numbers = search
        atoms = [
            Atom(int(Z), coords=coords) for Z, coords in zip(numbers, scaled_positions)
        ]

        # Preserve whatever subclass this object already is
        # This is important because some properties can be extracted from
        # source files (e.g. PWSCF output files)
        return type(self)(
            unitcell=atoms, lattice_vectors=lattice_vectors, source=self.source
        )

    def ideal(self, symprec=1e-2):
        """ 
        Returns a Crystal object with an idealized unit cell.
        
        Parameters
        ----------
        symprec : float, optional
            Symmetry-search distance tolerance in Cartesian coordinates [Angstroms].

        Returns
        -------
        ideal : Crystal
            Crystal with idealized cell. 

        Raises
        ------
        RuntimeError : If an ideal cell could not be found.
        
        Notes
        -----
        Optional atomic properties (e.g magnetic moment) might be lost in the symmetrization.
        """
        search = refine_cell(self._spglib_cell(), symprec=symprec)
        if search is None:
            raise RuntimeError("Ideal cell could not be found.")

        lattice_vectors, scaled_positions, numbers = search

        # Preserve whatever subclass this object already is
        # This is important because some properties can be extracted from
        # source files (e.g. PWSCF output files)
        return type(self)(
            unitcell=(
                Atom(int(Z), coords=coords)
                for Z, coords in zip(numbers, scaled_positions)
            ),
            lattice_vectors=lattice_vectors,
            source=self.source,
        )

    def supercell(self, n1, n2, n3):
        """
        Create a supercell from this crystal, i.e. an atomic structure where the crystal unit cell
        is duplicated along lattice vectors.

        Parameters
        ----------
        n1, n2, n3 : int
            Repeat along the `a1`, `a2`, and `a3` lattice vectors. For example, 
            ``(1, 1, 1)`` represents the trivial supercell.
        
        Returns
        -------
        cell : AtomicStructure
            Iterable of `crystals.Atom` objects following the supercell dimensions.
        """
        return Supercell(crystal=self, dimensions=(n1, n2, n3))

    def symmetry(self, symprec=1e-2, angle_tolerance=-1.0):
        """ 
        Returns a dictionary containing space-group information. This information 
        is computed from the crystal unit cell.
        
        Parameters
        ----------
        symprec : float, optional
            Symmetry-search distance tolerance in Cartesian coordinates [Angstroms].
        angle_tolerance: float, optional
            Symmetry-search tolerance in degrees. If the value is negative (default), 
            an internally optimized routine is used to judge symmetry.
        
        Returns
        -------
        info : dict
            Dictionary of space-group information. The following keys are available:

            * ``'international_symbol'``: International Tables of Crystallography 
              space-group symbol (short);

            * ``'international_full'``: International Tables of 
              Crystallography space-group full symbol;

            * ``'hall_symbol'`` : Hall symbol;

            * ``'hm_symbol'`` : Hermann-Mauguin symbol;

            *``'centering'``: Centering-type ("P", "F", etc.);

            * ``'pointgroup'`` : International Tables of 
              Crystallography point-group;

            * ``'international_number'`` : International Tables of 
              Crystallography space-group number (between 1 and 230);

            * ``'hall_number'`` : Hall number (between 1 and 531).
        
        Raises
        ------
        RuntimeError : if symmetry-determination has not succeeded.
        
        Notes
        -----
        Note that crystals generated from the Protein Data Bank are often incomplete; 
        in such cases the space-group information will be incorrect.
        """
        dataset = get_symmetry_dataset(
            cell=self._spglib_cell(), symprec=symprec, angle_tolerance=angle_tolerance
        )

        if dataset is None:
            raise RuntimeError("[SPGLIB] Symmetry-determination has not found a match.")

        spg_type = get_spacegroup_type(dataset["hall_number"])
        hm_symbol = Hall2HM[dataset["hall"]]

        # We do not distinguish between base-centered "A", "B", and "C"
        # "A" and "B" are translated to "C"
        centering = CenteringType(
            hm_symbol[0] if hm_symbol[0] not in {"A", "B"} else "C"
        )
        info = {
            "international_symbol": dataset["international"],
            "hall_symbol": dataset["hall"],
            "hm_symbol": hm_symbol,
            "centering": centering,
            "international_number": dataset["number"],
            "hall_number": dataset["hall_number"],
            "international_full": spg_type["international_full"],
            "pointgroup": spg_type["pointgroup_international"],
        }

        err_msg = get_error_message()
        if err_msg != "no error":
            raise RuntimeError(
                "[SPGLIB] Symmetry-determination has returned the following error: {}".format(
                    err_msg
                )
            )

        return info

    def symmetry_operations(self, symprec=1e-2):
        """
        Get the symmetry operations that the crystal unit cell respects. These symmetry operations
        are expressed in fractional coordinates.

        Parameters
        ----------
        symprec : float, optional
            Symmetry-search distance tolerance in Cartesian coordinates [Angstroms].
        
        Returns
        -------
        sym_ops : iterable of 2-tuples
            Each symmetry operations is a tuple of ``(rotation, translation)``.
            A rotation matrix is an array of shape (3,3), while ``translation`` is an array
            of shape (3,).

        Raises
        ------
        RuntimeError : if symmetry-determination has not succeeded.

        See also
        --------
        Crystal.reciprocal_symmetry_operations : symmetry operations in reciprocal basis
        """
        dataset = get_symmetry(cell=self._spglib_cell(), symprec=symprec)

        return [
            SymmetryOperation(r, t)
            for r, t in zip(dataset["rotations"], dataset["translations"])
        ]

    def reciprocal_symmetry_operations(self, symprec=1e-2):
        """
        Get the symmetry operations that the reciprocal unit cell respects. These symmetry operations
        are expressed in reciprocal fractional coordinates.

        Parameters
        ----------
        symprec : float, optional
            Symmetry-search distance tolerance in Cartesian coordinates [Angstroms].
        
        Returns
        -------
        sym_ops : iterable of 2-tuples
            Each symmetry operations is a tuple of ``(rotation, translation)``.
            A rotation matrix is an array of shape (3,3), while ``translation`` is an array
            of shape (3,).  

        Raises
        ------
        RuntimeError : if symmetry-determination has not succeeded.

        See also
        --------
        Crystal.symmetry_operations : symmetry operations in lattice basis
        """
        transformations = self.symmetry_operations(symprec=symprec)

        # Change of basis matrices allow to express
        # transformations in other bases
        to_reciprocal = change_of_basis(
            np.array(self.lattice_vectors), np.array(self.reciprocal_vectors)
        )
        from_reciprocal = np.linalg.inv(to_reciprocal)

        cast = lambda m: to_reciprocal @ m @ from_reciprocal

        return [
            SymmetryOperation(cast(rot), cast(trans)) for rot, trans in transformations
        ]

    @property
    def international_symbol(self):
        """ International Tables of Crystallography space-group short symbol. """
        return self.symmetry()["international_symbol"]

    @property
    def international_full(self):
        """ International Tables of Crystallography space-group full symbol. """
        return self.symmetry()["international_full"]

    @property
    def hall_symbol(self):
        """ Hall symbol. """
        return self.symmetry()["hall_symbol"]

    @property
    def hm_symbol(self):
        """ Hermann-Mauguin symbol. """
        return self.symmetry()["hm_symbol"]

    @property
    def pointgroup(self):
        """ International Tables of Crystallography point-group. """
        return self.symmetry()["pointgroup"]

    @property
    def international_number(self):
        """ International Tables of Crystallography space-group number (between 1 and 230). """
        return self.symmetry()["international_number"]

    @property
    def hall_number(self):
        """ Hall number (between 1 and 531). """
        return self.symmetry()["hall_number"]

    @property
    def centering(self):
        """ Centering type of this crystals. """
        return self.symmetry()["centering"]

    def __str__(self):
        """ String representation of this instance. Atoms may be omitted. """
        return self._to_string(natoms=10)

    def __repr__(self):
        """ Verbose string representation of this instance. """
        return self._to_string(natoms=len(self))

    def _to_string(self, natoms):
        """ Generate a string representation of this Crystal. Only include
         a maximum of `natoms` if provided. """

        # Note : Crystal subclasses need not override this method
        # since the class name is dynamically determined
        rep = "< {clsname} object with following unit cell:".format(
            clsname=self.__class__.__name__
        )
        atoms = islice(self.itersorted(), natoms)

        # Note that repr(Atom(...)) includes these '< ... >'
        # We remove those for cleaner string representation
        rep += "".join(
            "\n    " + repr(atm).replace("<", "").replace(">", "").strip()
            for atm in atoms
        )

        num_omitted_atms = len(self) - natoms
        if num_omitted_atms > 0:
            rep += "\n      ... omitting {:d} atoms ...".format(num_omitted_atms)
            rep += "\n      ... use repr() to show the full cell ... "

        # Lattice parameters are split between lengths and angles
        rep += "\nLattice parameters:"
        rep += "\n    a={:.3f}Å, b={:.3f}Å, c={:.3f}Å".format(
            *self.lattice_parameters[0:3]
        )
        rep += "\n    α={:.3f}°, β={:.3f}°, γ={:.3f}°".format(
            *self.lattice_parameters[3::]
        )

        # Show stochiometric information
        rep += "\nChemical composition:"
        for chem_symbol, composition in self.chemical_composition.items():
            rep += "\n    {s}: {p:.3f}%".format(s=chem_symbol, p=100 * composition)

        rep += "\nSource: \n    {} >".format(self.source or "N/A")
        return rep


class Supercell(AtomicStructure):
    """
    The :class:`Supercell` class is a set-like container that represents a 
    supercell of crystalline structures.

    It is recommended that you do not instantiate a :class:`Supercell` by hand, but rather
    create a ``Crystal`` object and use the ``Crystal.supercell`` method.

    To iterate over all atoms in the supercell, use this object as an iterable. 
    To recover the underlying crystal, use the ``Supercell.crystal`` attribute.

    Parameters
    ----------
    crystal : Crystal
        Crystal object from which the supercell is assembled.
    dimensions : 3-tuple of ints
        Number of cell repeats along the ``a1``, ``a2``, and ``a3`` directions. For example,
        ``(1, 1, 1)`` represents the trivial supercell.
    """

    def __init__(self, crystal, dimensions, **kwargs):
        self.crystal = crystal
        self.dimensions = dimensions

        n1, n2, n3 = self.dimensions

        atoms = list()
        for atm in crystal:
            for factors in product(range(n1), range(n2), range(n3)):
                fractional_offset = np.asarray(factors)
                newatm = Atom(
                    element=atm.element,
                    coords=atm.coords_fractional + fractional_offset,
                    lattice=self.crystal,
                    displacement=atm.displacement,
                    magmom=atm.magmom,
                    occupancy=atm.occupancy,
                )
                atoms.append(newatm)

        super().__init__(atoms=atoms)

    def __repr__(self):
        n1, n2, n3 = self.dimensions
        preamble = f"< Supercell object with dimensions ({n1} x {n2} x {n3}) and the following unit cell:\n"

        lines = repr(self.crystal).splitlines(keepends=True)
        lines[0] = preamble
        return "".join(lines)
