#!/usr/bin/env python3
"""Lay out PCB footprints in the same arrangement as their schematic symbols.

KiCad IPC API plugin. KiCad runs this script in its own Python environment
when the action is invoked from the PCB editor; it talks to the running
PCB editor through the kicad-python (kipy) library.
"""
import json
import os
import re
import sys
import traceback


DEBUG = None

ACTION_NAME = 'Schematic positions -> PCB positions'

# Schematic positions are stored in units of 0.01 mm and scaled by
# POS_SCALE (nm per unit) when placed on the board.
POS_SCALE = 5000

# Vertical spacing between sheets, as a multiple of each sheet's height.
OFFSET_FACTOR = 1.25


class UserError(Exception):
    """An error to report to the user without a traceback."""


# S-expression tokens: "(", ")", a double-quoted string (with backslash
# escapes) or a bare atom.
_TOKEN_RE = re.compile(r'\(|\)|"([^"\\]*(?:\\.[^"\\]*)*)"|[^\s()"]+', re.S)
_ESCAPE_RE = re.compile(r'\\(.)', re.S)
_ESCAPES = {'n': '\n', 'r': '\r', 't': '\t'}


def _unescape(s):
    if '\\' not in s:
        return s
    return _ESCAPE_RE.sub(lambda m: _ESCAPES.get(m.group(1), m.group(1)), s)


def parse_sexpr(text):
    """Parse a KiCad s-expression file into nested lists of strings."""
    stack = [[]]
    for m in _TOKEN_RE.finditer(text):
        token = m.group(0)
        if token == '(':
            node = []
            stack[-1].append(node)
            stack.append(node)
        elif token == ')':
            if len(stack) == 1:
                raise SyntaxError('unexpected )')
            stack.pop()
        elif m.group(1) is not None:
            stack[-1].append(_unescape(m.group(1)))
        else:
            stack[-1].append(token)
    if len(stack) != 1 or len(stack[0]) != 1:
        raise SyntaxError('unbalanced parentheses')
    return stack[0][0]


# Class to represent a single sheet of a schematic. Has a map from
# component IDs to positions, a map from sub-sheet IDs to sub-sheet
# names and schematic file names, plus coordinate ranges for the
# component positions.
class SchSheet:
    def __init__(self, file):
        self.file = file
        self.components = dict()
        self.sub_sheets = dict()

        print('New sheet from:', file, file=DEBUG)

        self.xrange = [None, None]
        self.yrange = [None, None]

        with open(file, encoding='utf-8') as f:
            self.walk(parse_sexpr(f.read()))

    # Extend x- and y-coordinate ranges based on new component or
    # sub-sheet values.
    def extend_range(self, x, y):
        if self.xrange[0] is None or x < self.xrange[0]:
            self.xrange[0] = x
        if self.xrange[1] is None or x > self.xrange[1]:
            self.xrange[1] = x
        if self.yrange[0] is None or y < self.yrange[0]:
            self.yrange[0] = y
        if self.yrange[1] is None or y > self.yrange[1]:
            self.yrange[1] = y

    def height(self):
        if self.yrange[0] is None:
            return 0
        return self.yrange[1] - self.yrange[0]

    @staticmethod
    def pick(lst, *attribute_names):
        names = set(attribute_names)
        return {item[0]: item[1:] for item in lst
                if isinstance(item, list) and item and item[0] in names}

    @staticmethod
    def pick_property(lst, prop_name):
        for item in lst:
            if (isinstance(item, list) and len(item) > 2
                    and item[0] == 'property' and item[1] == prop_name):
                return item[2]

    def walk(self, ast):
        def position_convert(x):
            return int(float(x) * 100)

        for i in ast:
            if not isinstance(i, list) or not i:
                continue
            token = i[0]
            if token == 'symbol':  # Placed component
                attrs = self.pick(i[1:], 'uuid', 'at')
                component_ref = self.pick_property(i[1:], 'Reference')
                component_id = attrs['uuid'][0]
                component_pos = tuple(map(position_convert, attrs['at'][:2]))
                self.components[component_id] = (component_ref, component_pos)
                self.extend_range(component_pos[0], component_pos[1])
            elif token == 'sheet':  # Sub-sheet
                attrs = self.pick(i[1:], 'uuid', 'at', 'size')
                sheet_bounds = tuple(map(position_convert, attrs['at'][:2] + attrs['size'][:2]))
                sheet_id = attrs['uuid'][0]
                sheet_name = self.pick_property(i[1:], 'Sheetname')
                sheet_file = self.pick_property(i[1:], 'Sheetfile')
                self.sub_sheets[sheet_id] = (sheet_name, sheet_file)
                self.extend_range(sheet_bounds[0], sheet_bounds[1])
                self.extend_range(sheet_bounds[0] + sheet_bounds[2],
                                  sheet_bounds[1] + sheet_bounds[3])


def root_schematic_files(work_dir, project_name):
    """Return the project's top-level schematic files.

    KiCad 10 projects can have several top-level sheets, listed in the
    project file. Older projects have a single root sheet named after
    the project.
    """
    project_file = os.path.join(work_dir, project_name + '.kicad_pro')
    try:
        with open(project_file, encoding='utf-8') as f:
            settings = json.load(f)
        top_level = settings.get('schematic', {}).get('top_level_sheets') or []
        files = [os.path.join(work_dir, s['filename'])
                 for s in top_level if s.get('filename')]
        if files:
            return files
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return [os.path.join(work_dir, project_name + '.kicad_sch')]


def load_sheets(root_files):
    """Read each top-level sheet and its hierarchy, in layout order.

    Returns a list of (sheet_path, SchSheet). Symbols on a top-level sheet
    have footprint paths of the form /<symbol uuid>; symbols on sub-sheets
    are prefixed by the uuids of the sheets above them.
    """
    sheets = []
    for root_file in root_files:
        queue = [('', root_file)]
        while queue:
            sheet_path, file_name = queue.pop(0)
            sheet = SchSheet(file_name)
            print('store to sheet[{}] = {}'.format(sheet_path, file_name), file=DEBUG)
            sheets.append((sheet_path, sheet))
            # Sub-sheet file names are relative to the parent sheet's directory.
            base_dir = os.path.dirname(file_name)
            for sub_id, (_, sub_file) in sheet.sub_sheets.items():
                queue.append((sheet_path + '/' + sub_id,
                              os.path.join(base_dir, sub_file)))
    return sheets


def component_targets(sheets):
    """Map each symbol's footprint path to (ref, sheet_path, x_nm, y_nm).

    Sheets are laid out one after another down the page, each offset by
    the heights of the sheets before it.
    """
    targets = dict()
    running_offset = 0
    for sheet_path, s in sheets:
        offset = int(running_offset)
        running_offset += OFFSET_FACTOR * s.height()
        print('Processing', sheet_path or '/', 'offset', offset, file=DEBUG)
        for cid, (ref, pos) in s.components.items():
            targets[sheet_path + '/' + cid] = (
                ref, sheet_path, pos[0] * POS_SCALE, (pos[1] + offset) * POS_SCALE)
    return targets


def schematic_targets(work_dir, project_name):
    root_files = root_schematic_files(work_dir, project_name)
    print('root_schematic_files = {}'.format(root_files), file=DEBUG)
    missing = [f for f in root_files if not os.path.isfile(f)]
    if missing:
        raise UserError('Schematic not found: ' + ', '.join(missing))
    targets = component_targets(load_sheets(root_files))
    for path in targets:
        print(path, targets[path], file=DEBUG)
    return targets


def move_footprint(fp, position):
    """Move a footprint and all of its child items to a new position."""
    from kipy.board_types import Barcode, BoardTextBox

    delta = position - fp.position
    # Moves the footprint's fields, pads, text, shapes and zones.
    fp.position = position
    # kipy does not move these child items itself.
    for item in fp.definition.items:
        if isinstance(item, BoardTextBox):
            item.top_left = item.top_left + delta
            item.bottom_right = item.bottom_right + delta
        elif isinstance(item, Barcode):
            item.position = item.position + delta


def board_file_path(board):
    board_file = board.name
    if not board_file:
        raise UserError('Save the board as part of a KiCad project first.')
    if not os.path.isabs(board_file):
        board_file = os.path.join(board.get_project().path, board_file)
    return os.path.abspath(board_file)


def move_footprints(board, targets):
    from kipy.geometry import Vector2
    from kipy.proto.common.types import KiCadObjectType

    footprints = board.get_footprints()
    selected = {item.id.value for item in
                board.get_selection(types=[KiCadObjectType.KOT_PCB_FOOTPRINT])}

    to_update = []
    for fp in footprints:
        ref = fp.reference_field.text.value
        path = '/' + '/'.join(kiid.value for kiid in fp.sheet_path.path)
        print(ref, path, file=DEBUG)
        if path not in targets:
            print('  NOT FOUND', file=DEBUG)
            continue
        ref, sheet, x, y = targets[path]
        if fp.locked:
            print('  path =', path, '  sheet =', sheet, '  ref =', ref, ' is locked, skip', file=DEBUG)
            continue
        print('  path =', path, '  sheet =', sheet, '  ref =', ref,
              '  new_pos =', (x, y), file=DEBUG)
        if not selected or fp.id.value in selected:
            move_footprint(fp, Vector2.from_xy(x, y))
            to_update.append(fp)

    # Apply all moves as a single undoable step.
    if to_update:
        commit = board.begin_commit()
        try:
            board.update_items(to_update)
        except Exception:
            board.drop_commit(commit)
            raise
        board.push_commit(commit, ACTION_NAME)
    print('moved', len(to_update), 'footprints', file=DEBUG)
    return len(to_update)


def main():
    global DEBUG
    debug_file = None
    try:
        try:
            from kipy import KiCad
        except ImportError:
            raise UserError('The kicad-python package is not installed in this '
                            "plugin's Python environment.")

        board = KiCad(timeout_ms=60000).get_board()
        board_path = board_file_path(board)
        work_dir, in_pcb_file = os.path.split(board_path)
        project_name = os.path.splitext(in_pcb_file)[0]

        debug_file = os.path.join(work_dir, 'schematic-positions-to-layout.debug')
        DEBUG = open(debug_file, 'w', encoding='utf-8')
        print('work_dir = {}'.format(work_dir), file=DEBUG)
        print('in_pcb_file = {}'.format(in_pcb_file), file=DEBUG)

        targets = schematic_targets(work_dir, project_name)
        move_footprints(board, targets)
        return 0
    except Exception as e:
        if DEBUG is not None:
            traceback.print_exc(file=DEBUG)
        elif not isinstance(e, UserError):
            traceback.print_exc()
        message = str(e) or type(e).__name__
        if debug_file and not isinstance(e, UserError):
            message += ' (details in {})'.format(debug_file)
        print(message, file=sys.stderr)
        return 1
    finally:
        if DEBUG is not None:
            DEBUG.close()
            DEBUG = None


if __name__ == '__main__':
    sys.exit(main())
