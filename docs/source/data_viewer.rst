Data viewer
===========

The Data viewer is one window for looking at data: the acquisition that is
running, shown as the camera delivers it, and datasets that are already on
disk. It is in the **View** menu.

**View → Open Data Viewer** opens the window, or brings it to the front. It
watches the folder the acquisition list saves into. The newest acquisition in
that folder is shown, and when an acquisition starts it is shown in its place
and grows on screen plane by plane.

**View → Open Dataset in Data Viewer...** asks for a dataset that is already on
disk and shows it in the same window, beside whatever is there. The folder
button at the top of the window's panel does the same, and so does dropping a
folder from the file manager onto the window. You can pick:

* one acquisition, the ``<Sample>.ome.zarr`` folder holding its tiles, to see
  all the tiles together;
* one tile, a ``Mag…_Tile…_Sh…_Rot….ome.zarr`` store, to see it on its own;
* a data folder holding several acquisitions: every one is listed, and the
  newest is shown.

The window is a neuroglancer page driven from Python, the ``mesospim_viewer``
package in ``mesoSPIM/src/mesospim_viewer/``.

A running acquisition
---------------------

While the microscope acquires, the stack that is being acquired is drawn from
the camera's frames, the same frames the camera window shows. So the first
plane is on screen as soon as the camera has delivered it, and this works
whatever format the run is saved in. The picture of a running stack is a
thinned-out copy held in memory; once the stack is complete on disk, the tile
on disk takes its place. With an OME-Zarr writer that happens stack by stack.
A run saved as TIFF, raw or HDF5 cannot be read while it is written, so it
stays on screen as the thinned-out copy until the whole run is over, and is
then shown from its files, at full size.

Large acquisitions
------------------

An acquisition of twelve tiles or more, once all its tiles are finished, is
given one small picture of the whole. While you are zoomed out, the viewer
draws that picture in place of the tiles: from far away it shows the same, and
it is one thing to draw where a hundred tiles are a hundred. As you zoom in,
the tiles themselves take over from the point where the small picture would
start to look coarse. You do not have to do anything, and there is nothing to
see of the change but that moving around a large acquisition stays quick.

The small picture is made in memory from each tile's own smaller copies, a
moment after the acquisition is shown. Nothing is written to disk. Where two
tiles overlap it keeps the brighter of the two, as the viewer does when it
draws the tiles. A run that is still being acquired has none: its tiles are
drawn as they are until it is over.

Runs saved as TIFF, raw or HDF5
-------------------------------

A run saved with ``Tiff_Writer``, ``Big_Tiff_Writer``, ``RAW_Writer`` or
``H5_BDV_Writer`` can be opened like any other: with **Open…** (choose *A
file* and pick any one file of the run), by dropping a file on the window, or
from the list, where the runs of the data folder appear beside the OME-Zarr
acquisitions. Nothing is converted and nothing is written to disk.

* The TIFF or raw files of one run are told by their names. The words that
  say which tile, channel, side or angle a file is (``Tile3``, ``Ch488``,
  ``Sh0``, ``Rot0``) are left out, and files that agree in the rest are one
  run. An HDF5 file holds a whole run by itself.
* Where a stack lies and which channel it is comes from what mesoSPIM writes
  beside the data: the ``_meta.txt`` file of a TIFF or raw stack, and the
  ``.xml`` file of an HDF5 file. Keep those files with the data. A TIFF
  without its ``_meta.txt`` still opens, at the origin and with a voxel
  counted as one micrometre; a raw file without it cannot be opened, because
  nothing else says how large a plane is.
* These files have no smaller copies of the picture, which a view of a whole
  acquisition needs. The viewer makes them up as it reads, by taking every
  second voxel, every fourth, and so on. Zoomed in, such a run is as quick as
  any. Zoomed out on many large tiles it is slower than OME-Zarr, most of all
  for HDF5, where whole blocks have to be read to take a few voxels out of
  each. For data you look at often, the OME-Zarr writers remain the better
  choice.
* An HDF5 file can hold smaller copies of its own, if the writer is told to
  make them: ``H5_BDV_Writer = {'subsamp': ((1, 1, 1), (1, 4, 4), (4, 16, 16)), ...}``
  in the configuration file (the three numbers are the steps in z, y and x).
  The viewer reads from those copies, so the view from far away is quick, and
  a run of twelve tiles or more gets its overview picture like an OME-Zarr run
  does. Imaris opens only files without such copies, so leave ``subsamp`` as it
  is if you need the file there.
* Maximum projections (``MAX_...``) are not listed.

* The **Live** button at the top left is lit while the view follows the
  acquisition, which means it shows the newest plane. If you step through the
  planes or time points yourself, the view stays where you put it and the
  button reads **Back to live**; press it to follow again.
* The view zooms out to show every tile as tiles arrive, until you pan or zoom.
  After that the **Fit** button lights up; press it to see everything again.
  Zooming into the tile that is being acquired does not stop the planes from
  coming.
* The contrast of each channel sets itself from what is on screen while the
  run is going on (**Auto** in the channel's row is lit). Moving a channel's
  contrast yourself switches Auto off for that channel; press **Auto** to
  switch it on again.

The viewer does not slow the acquisition down: it takes a small copy of a
frame a few times a second and does everything else in its own threads. It
also keeps off the drive the microscope is writing to. A tile is listed once
when it first appears; a finished tile is not listed again; and the tile being
acquired is checked on disk only every ten seconds, because it is on screen
from the camera's frames until its stack is complete.

What is on screen
-----------------

* **2D / 3D** switch, top left of the picture. 3D draws a maximum projection;
  the mouse turns it.
* **Depth (Z) slider** and **time (T) slider** along the bottom, each only
  when there is more than one plane or time point. The Z slider shows the
  depth in micrometres and the plane number.
* **Fit**, beside the 2D / 3D switch: shows every tile again. It is dimmed
  while the view already shows everything.
* **Under the pointer**: the position in micrometres and the value of each
  channel, beside the channel's colour, in the top right corner.
* **Panel** on the right, foldable:

  * In its head, two small buttons. **Open…** adds a saved acquisition, and
    **Live** decides whether what the microscope acquires comes in. With Live
    on, a run that starts appears at the top of the list, marked **Live**, and
    turns **Saved** when it ends. With Live off, nothing new comes in, and a
    run being shown is marked **Paused** until Live is on again.
  * *Acquisitions*: those of the watched folder, newest first, and the
    datasets you opened, each a bar with an eye, its name, one line about it
    and Live, Saved or Paused. The eye shows or hides it, so several can be
    shown together, side by side where they lie. A click on the bar selects it,
    folds out the map of its positions (one acquisition's at a time; the arrow
    folds it away) and takes the view to all of it. The × takes a saved
    acquisition off the list; its files stay on disk. It shows for the
    acquisition you are on.
  * *The map of the positions*: every tile of the selected acquisition as a
    cell, where it lies on the stage. This works the same for four tiles and
    for four hundred. A dashed frame marks the part of the specimen that is on
    screen. During a run the cells show how far it has got: filled when
    acquired, green while being acquired, dashed while still to come. A click
    on a cell takes the view to that tile. An acquisition that is not shown has
    its map too, a moment after it is listed; a click on one of its cells
    shows it and goes to the tile. Tiles that lie on exactly the same spot
    (the same place acquired at another angle, or lit from the other side) are
    drawn one inside the other, so each can be pointed at and clicked.
  * *The map and the picture answer each other.* Point at a cell, and a box
    is drawn around that tile in the picture. Point at a tile in the picture,
    and its cell lights up, with its name under the map. A click on a tile in
    the picture selects it, and with it its acquisition, so the channels below
    become that acquisition's; the view stays where it is. Dragging still
    moves the picture: only a press and release in one place is a click. A
    click where no tile lies takes the selection off again.
  * *Channels*: the selected acquisition's channels, one row each, with an
    eye, a colour swatch, **Auto**, and the contrast with a thin strip of its
    histogram. A click on the channel's name opens the histogram larger.
  * *3D view*, in 3D only, above the channels: the projection (max, blend, min), the detail, the
    gain, where to look from, and whether to draw the plane the Z slider moves.
    **Detail** sets how sharply the volume is drawn for what is on screen, from
    fast to as fine as the screen; beside it, the size of a voxel of the copy of
    the image drawn now. Zooming in draws from finer copies by itself, down to
    the finest the data has, so to look at a detail, zoom in on it. While you
    turn, zoom or pan it, the volume is drawn coarser so that it keeps up, and
    at the detail chosen again as soon as you stop. The volume
    turns about the middle of the stack; while an acquisition runs, Live in 3D
    follows its time points.

* The picture is never silently black: with nothing to draw it says what it is
  waiting for, and a thin bar along the top shows while data is still loading.

Moving around
~~~~~~~~~~~~~

.. list-table::
   :widths: 40 60

   * - Drag
     - move the picture
   * - Scroll
     - zoom in and out
   * - Shift + scroll, or ``,`` and ``.``
     - step through the planes
   * - ``[`` and ``]``
     - step through the time points
   * - Drag, in 3D
     - turn the specimen (Shift + drag moves it)

The **?** button at the top right of the picture shows this list.

Channels are mixed so that a structure that is lit in two channels shows in
both colours. Tiles are placed by their stage positions and are not stitched:
where two tiles overlap, the brighter of the two is seen.

Which data it can show
----------------------

From disk the viewer reads OME-Zarr in version 0.4 or 0.5, with the axes
``t, c, z, y, x`` or some of them in that order, as long as ``z, y, x`` are
there. So ``(z, y, x)``, ``(c, z, y, x)`` and ``(t, z, y, x)`` open too. An
axis that is missing simply counts as one step long. OME-Zarr 0.6 is not read
yet; the viewer says so if you pick one.

That covers what the ``MP_OME_Zarr_TCZYX_Writer`` saves (one
``(t, c, z, y, x)`` store per tile, see :doc:`file_formats`) and what the
``MP_OME_Zarr_Writer`` saves (one store per tile and channel). Older data
from the ``OME_Zarr_Writer``, which has since been removed from mesoSPIM, has
that same layout and opens too.

When a dataset holds one store per tile *and* per channel, the viewer needs to
know which channel each store holds. It reads this from the ``omero`` block
inside the store, never from the file name. Stores without that information
cannot be told apart, so their channels are then shown on top of each other as
one channel.

.. note::

   **For a quick viewer, keep one store per position.** What makes the viewer
   slow is the number of stores, not which axes they have, because every store
   is set up on its own when it is shown. One store per position, with all its
   channels and time points inside it (``t, c, z, y, x``), stays quick even with
   hundreds of positions. A dataset split into one store per channel or per time
   point opens too, but takes longer the more stores it has.

Installing
----------

Nothing extra is needed. The viewer comes with mesoSPIM-control, its page
already built (no Node needed), and the web view for PyQt5 that it draws in,
PyQtWebEngine, is installed together with mesoSPIM-control, both by
``pip install -e .`` and by ``pip install -r requirements-conda-mamba.txt``.

An environment set up before the viewer existed does not have it yet. In
that case, install it into the mesoSPIM Python environment and restart
mesoSPIM, because mesoSPIM loads the web view only at start-up:

.. code-block:: bash

   pip install PyQtWebEngine==5.15.7

If it is missing, the menu entry shows a message saying so, and nothing else
changes.

Testing it
----------

Before the first real acquisition, from a Python prompt in that environment:

.. code-block:: python

   from mesoSPIM.src import mesospim_viewer; import PyQt5.QtWebEngineWidgets   # both must import
   mesospim_viewer.Viewer().page_built                                         # must be True

Then, without the microscope, a rehearsal of a run in the Data viewer window:

.. code-block:: bash

   python -m mesoSPIM.src.mesospim_viewer.demo --live --window

It acquires four two-channel tiles with the microscope's own OME-Zarr writer,
frame by frame at a camera's pace, and feeds the window the frames the way
mesoSPIM-control does. What to look for:

1. The window opens saying *No acquisition yet*. Within a second or two the
   first tile appears and fills in plane by plane; **Live** is lit, and the Z
   slider moves on its own.
2. Each further tile appears beside the others as it starts, and the view
   zooms out to show them all.
3. Drag the Z slider back: the view stays there and the button reads
   **Back to live**. Press it: the view follows the newest plane again.
4. Hiding a channel with its eye, or changing its colour, stays as it is when
   the next tile starts.
5. 3D shows the tiles as a volume; **Top / Front / Side** turn it.
6. A second run of the same command starts ``run_01`` in the same folder: the
   window shows it in place of ``run_00``, which stays in the list.

Then with the microscope: select ``MP_OME_Zarr_TCZYX_Writer`` in the
file-naming wizard, open the Data viewer, and run a short acquisition list of
two tiles and two lasers. Each stack should fill in as it is acquired, and a
time lapse of a few time points should extend the T slider. In the panel,
while it runs and after:

1. The run comes in at the top, marked **Live**, opened, with the map of its
   positions. All tiles of the run are on the map from the first stack on:
   the one being acquired is green, the ones still to come are dashed. Point
   at a cell to read its state under the map; with the list going laser by
   laser, a tile with one laser done says *1 of 2 channels*.
2. At the end it turns **Saved**, its line says how many positions and which
   channels, and it has a ×.
3. A click on an earlier acquisition selects it: its map folds out, the
   other's folds away, the channels below become its own, and the view goes to
   it. A click on a cell of the map goes to that tile; **Fit** goes back.
   Pointing at a tile in the picture boxes it and lights its cell; a click on
   it selects it without moving the view.
4. **Live** off, and a short run: it does not appear. **Live** on again: it
   appears, **Saved**. **Live** off during a run: it reads **Paused**; on again,
   it goes on.
5. **Open…** adds a saved acquisition; the × takes one off the list
   (its files stay on disk). File > Exit closes the Data viewer with mesoSPIM.

Giving the viewer more memory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The viewer keeps the parts of the image it is drawing on the graphics card,
and the parts it drew a moment ago in the computer's memory. When it runs out
of room it lets go of what it drew longest ago, and has to load that again
when you come back to it. With more room, a large acquisition (above all as a
volume) is drawn in finer detail and comes back faster when you return to a
place.

You do not have to set anything. When the Data viewer opens it looks at what
the computer has and takes a modest share:

* **half of the memory of the graphics card it draws on**, and never more than
  4000 MB. A computer can have two graphics cards, a small one built into the
  processor and a large one beside it. The viewer asks its own window which
  of them draws the picture, and only that card counts.
* **an eighth of the computer's memory**, and never more than 8000 MB. The
  camera, the file writer and the rest of mesoSPIM keep the rest.

If the viewer cannot find out what the computer has, or the share would be
smaller than 1000 MB on the graphics card or 2000 MB in memory, it keeps those
two limits, which are safe on any computer. mesoSPIM's log says what it chose,
in lines that begin with ``The Data viewer keeps``.

To choose yourself, add this to the configuration file, with numbers that fit
the computer (both are in megabytes; one that is left out is still chosen by
the viewer):

.. code-block:: python

   data_viewer = {
       'gpu_memory_MB': 4000,      # on the graphics card; keep it well below what the card has
       'system_memory_MB': 8000,   # in the computer's memory; the camera and the writer need room too
   }

The new limits apply the next time the Data viewer is opened. If an entry is
not a positive number, mesoSPIM's log says so and the viewer chooses for it.
To go back to the small limits that are safe anywhere, write 1000 and 2000.

Checks that need the microscope PC
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Some things can only be checked on the Windows PC of the microscope: the
window itself (it needs a real screen and graphics card), and what the writer
does when Windows, not Linux, looks after the files. Demo mode
(``python mesoSPIM/mesoSPIM_Control.py -D``) is enough for all of them; no
laser has to be on.

First the automatic tests, from the folder that holds ``mesoSPIM``, in the
environment mesoSPIM runs in. They take a few minutes.

.. code-block:: bat

   pip install pytest pytest-timeout playwright
   playwright install chromium
   set MESOSPIM_VIEWER_QT_TESTS=1
   python -m pytest mesoSPIM/test/mesospim_viewer mesoSPIM/test/test_omezarr_tczyx_writer.py mesoSPIM/test/test_tiff_to_omezarr.py -q --timeout=600

``MESOSPIM_VIEWER_QT_TESTS=1`` switches on the tests that open the real
window. Everything should pass. One test, ``test_realistic.py ... [feed]``,
measures timing and can fail while the PC is busy with something else; run it
again on its own before reading anything into it.

Then the rehearsal. It opens the Data viewer window itself, on the PC's own
screen and graphics card, and goes through what an operator would: a saved
acquisition, data from the microscope's writer with and without shards, a run
followed live, a time lapse, 2D and 3D, the map, a large acquisition with its
overview, and runs saved as TIFF and HDF5. It needs no microscope and does not
start mesoSPIM. A window is on screen while it works; leave it alone for the
few minutes it takes.

.. code-block:: bat

   python -m mesoSPIM.test.mesospim_viewer.rehearsal

Every step is reported as PASS or FAIL with a sentence about what was seen,
and the whole report is also written to ``data_viewer_rehearsal.txt`` in the
folder you started it from. Send that file along if something fails. Three
parts of the report are worth reading even when everything passes:

* **How it moves on this PC.** The time between two frames while the picture
  is panned, zoomed and turned. About 17 ms is as smooth as a 60 Hz screen can
  show; above 50 ms it feels slow.
* **The large acquisition.** How long a hundred tiles take to appear, and how
  much the engine does for one frame zoomed out and zoomed in. Zoomed out the
  number should be small (about ten): the overview picture stands in for the
  tiles. ``--tiles 300`` tries a larger one.
* **Memory.** What the graphics card and the computer have, and, if there is
  room, a ``data_viewer`` line for the configuration file.

Then by hand, in demo mode with the Data viewer open. The rehearsal has already
covered checks 1, 9, 10, 11 and 12 as far as a script can; do those by hand
only to see for yourself how it feels. The others happen in mesoSPIM's own
windows, or need a real Stop, and a script outside mesoSPIM cannot do them:

1. **Data saved in shards shows up.** Choose ``MP_OME_Zarr_Writer`` with
   ``'ome_version': '0.5'`` and its default ``'shards'``, run two tiles, and
   wait until the run turns **Saved**. The tiles must stay visible, sharp when
   zoomed in. Close the window, open it again and load the run with
   **Open…**: it must appear again. (Sharded data is the default of this
   writer, and the window's browser is an older one than the tests usually
   run in, so it is worth seeing once on the real PC.)
2. **The writer's own settings in the wizard.** In the file-naming wizard
   choose *MP OME Zarr TCZYX*. The page of settings should list eight rows,
   with ``shards`` set to ``None`` and no BigStitcher or write-cache rows.
3. **Rows run one at a time.** With ``MP_OME_Zarr_TCZYX_Writer``, make a list
   of two tiles and two lasers, all under one file name. Run the rows one by
   one with *Run selected acquisition*, in any order. The acquisition folder
   should hold two tile stores, and in the viewer each tile should show both
   channels.
4. **One laser with two filters.** Give one tile two rows with the same laser
   and different filters, and run the list. The viewer should show two
   channel rows, named after the laser and the filter.
5. **Stop in the second channel.** Run a tile with two lasers and press Stop
   halfway through the second stack. In the viewer the second channel should
   end where it was stopped, with nothing after it. Start the same list again
   under a new file name; it should run through.
6. **A very short stack.** Run a stack of two or three planes with either
   OME-Zarr writer. It must be saved. mesoSPIM's log must not contain
   ``FileNotFoundError`` or ``PermissionError`` from the writer.
7. **A time lapse with the viewer open.** Three time points of two tiles with
   ``MP_OME_Zarr_TCZYX_Writer``. The T slider should grow to three, and the
   log should again be free of ``PermissionError``. The tiles of the first
   time point must stay on the picture, without a flicker, while the second
   and third are acquired.
8. **Closing and resizing.** Make the window larger and smaller while a saved
   run is shown: the picture should stay fitted. Close the window during a
   run: mesoSPIM must stay responsive and the run must go on. Open the viewer
   again afterwards; it should pick the run up.
9. **The map and the picture.** Run a grid of at least three by three tiles.
   From the first stack on, the map in the panel should show all nine cells:
   the one being acquired green, the others dashed, then filled as they are
   done. Afterwards, point at tiles in the picture: a box should follow the
   pointer from tile to tile, and the matching cell should light up. Click a
   tile: its cell turns blue and the view does not move. Click a cell: the
   view goes to that tile. Open a second acquisition beside the first and
   click one of its tiles: the channel rows should become that acquisition's.
10. **How it feels.** Pan, zoom and drag the Z slider through a saved run.
    Movement should follow the mouse without a pause, and tiles should
    sharpen within a moment of stopping. Switch between 2D and 3D a few times:
    from the second time on the volume should be there at once, without a
    black picture in between. Zoom and turn the volume: it is drawn coarser
    while it moves and sharp when it stops, and should never disappear.
11. **A large acquisition.** If you have one with a hundred tiles or more,
    open it during a run of something else. The tiles should appear a few at
    a time over some seconds; meanwhile the live view of the run must keep
    moving and mesoSPIM must stay responsive. Watch the acquisition drive's
    activity for a few minutes afterwards: once the tiles are in, the viewer
    should leave the drive alone. An acquisition from an earlier day should be
    there within seconds even from a network drive, because its tiles are
    taken in without listing their folders. Zoomed all the way out, panning
    and the Z slider should feel as quick as for a single tile; zoom in on one
    tile and it should turn sharp, with no moment in between where the picture
    is empty.
12. **A map before the acquisition is shown.** With two acquisitions in the
    list, click the one that is not shown (its eye is crossed out) to fold its
    positions out. After a moment its map should be there. A click on a cell
    should show the acquisition and go to that tile.
13. **After a time lapse.** Run a time lapse of two time points, with any
    writer. While it runs, the file names in the acquisition table end in
    ``_Time000``, then ``_Time001``. When it has finished, and also when you
    stop it halfway, the names should be back to what they were before.
14. **The writer process is ended.** Start a long stack with one of the
    OME-Zarr writers. In the Windows Task Manager, end the newest ``python``
    process (the writer runs in a process of its own). Within a few seconds
    mesoSPIM's status line should read ``NOT SAVING``, the log should say that
    the writer process ended unexpectedly, and the acquisition should go on to
    the next stack, which is saved as usual. Stop must still work.
15. **The same time point once more.** With ``MP_OME_Zarr_TCZYX_Writer``, run a
    time lapse of one time point of two tiles, wait more than ten minutes, and
    run it again under the same name. In the viewer the tiles should be
    replaced by the new data as it is written, without closing the window.
16. **A run saved as TIFF, and one as HDF5.** Run two tiles in two channels
    with ``Tiff_Writer``. While it runs the picture comes from the camera's
    frames, and the data folder's list shows the run once. A moment after the
    run has ended, zoom in: the picture should now be sharp, because it is read
    from the files. Close the Data viewer, open it again, choose **Open…**,
    *A file*, and pick any one of the run's TIFF files: all four stacks should
    appear, as two tiles with two channels. Repeat with ``H5_BDV_Writer``.
17. **Do neighbouring tiles line up?** With real data from the microscope, look
    at the strip two neighbouring tiles share. A structure in it should be in
    the same place in both. If the tiles look mirrored against each other, or
    the mosaic is laid out the wrong way round, write down which writer saved
    the run: OME-Zarr and TIFF runs are placed by the stage position as it is,
    HDF5 runs as BigStitcher would place them, and only real data shows whether
    both are right.

If something is wrong
---------------------

* **The window stays black and says nothing**: usually WebGL in the Qt web
  view. Set ``QTWEBENGINE_CHROMIUM_FLAGS=--ignore-gpu-blocklist`` in the
  environment before starting, and try ``--disable-gpu-driver-bug-workarounds``
  after that. ``python -m mesoSPIM.src.mesospim_viewer.demo --live`` (without
  ``--window``) shows the same rehearsal in the system browser: if the browser
  draws and Qt does not, it is Qt's GPU path and not the viewer.
* **The picture is there but dark**: press **Auto** in the channel's row.
* **The menu entry says PyQtWebEngine is missing** although it is installed:
  it was installed into a different Python environment than the one mesoSPIM
  runs in. ``python -c "import PyQt5.QtWebEngineWidgets"`` from the mesoSPIM
  environment tells.
* **"QtWebEngineWidgets must be imported before a QCoreApplication instance is
  created"**: PyQtWebEngine was installed after mesoSPIM was started, or the
  ``mesoSPIM_DataViewer.prepare_qt()`` call at the top of ``mesoSPIM_Control.py``
  was removed. Restart mesoSPIM.
* **A tile fills in only in large steps, or late**: the window is not getting
  the camera's frames and is following the files on disk instead, which arrive
  64 planes at a time. This is the case when the window was started on its own
  (``python -m mesoSPIM.src.mesospim_viewer.window <folder>``) rather than from
  the View menu.
* **A finished run saved as TIFF, raw or HDF5 stays coarse**: the viewer could
  not read its files when the run ended, and went on showing the thinned-out
  copy made from the camera's frames. It tries for a minute and a half. Open
  the run again with **Open…**; if a file cannot be read, the picture says why.
* **All channels look the same in a dataset from disk**: the dataset holds one
  store per channel, and the stores do not say which channel they hold (they
  have no ``omero`` block). The viewer then cannot tell them apart. This is the
  case for data that the ``MP_OME_Zarr_Writer`` (or the older, now removed
  ``OME_Zarr_Writer``) wrote before the channel was saved in each store; data
  written since shows each channel as its own row.
* **The data viewer's own tests**: ``python -m pytest mesoSPIM/test/mesospim_viewer``,
  with Playwright and a Chromium for the picture tests (they skip, saying so,
  without them). Changing the page itself needs Node: see
  ``mesoSPIM/src/mesospim_viewer/README.md``.
