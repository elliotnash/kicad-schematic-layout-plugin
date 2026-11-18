# Miscellaneous plugins for KiCad

## Reproduce schematic layout in PCB layout

Based on an original idea by @jenschr

Supported versions: KiCad 7, 8, 9 and 10.

The plugin uses KiCad's SWIG Python API, which is deprecated since
KiCad 9 and removed in KiCad 11, so it will not run on KiCad 11 or
later.

The `SchematicPositionsToLayout.py` plugin helps with the initial
organisation of parts when first creating a PCB layout from a
schematic.

### Installation

Copy this whole folder (`__init__.py`, `SchematicPositionsToLayout.py`
and `sch2layout.png`) into your KiCad scripting plugins folder. To find
it, open the PCB Editor and choose "Tools" -> "External Plugins" ->
"Open Plugin Directory". On Linux it is
`~/.local/share/kicad/10.0/scripting/plugins/`.

Then choose "Tools" -> "External Plugins" -> "Refresh Plugins", or
restart KiCad.

### Usage

1. Create a new PCB layout and run "Tools" -> "Update PCB from
   Schematic..." to bring in the footprints.

2. Select "Schematic positions -> PCB positions" from the "Tools" ->
   "External Plugins" menu, or click its toolbar button.

The result of this action will be that the component footprints in
the PCB editor will be laid out in the same pattern as their
corresponding components in the schematic. Hierarchical sheets, and
each top-level sheet of a KiCad 10 project with several of them, are
laid out one after another down the page in non-overlapping areas.

If any footprints are selected, only those footprints are moved.
Locked footprints are never moved.

The movement of the component footprints by the plugin is a normal
editing action, so can be undone if you don't like what you see.

Note that the footprint organisation produced by this script is
intended only to aid with keeping track of where everything is at the
very start of component placement. For example, if you have a large
number of decoupling capacitors for a component, you can place them
near to the component in the schematic and the plugin will move them
to be near to the component in the layout too. Another example where
this might be useful is for analog sections where it might be useful
to mirror signal flow and component placement on the schematic in an
initial view of the PCB layout.
