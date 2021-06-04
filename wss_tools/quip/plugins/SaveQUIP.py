"""
Save images to output files.

**Plugin Type: Local**

``SaveQUIP`` is a local plugin, which means it is associated with a
channel. An instance can be opened for each channel.

**Usage**

This plugin is only used in ``ANALYSIS`` mode, as defined in
:ref:`quip-doc-ginga-files`.

It is very much like
:ref:`SaveImage (Save File) in Ginga <ginga:sec-plugins-global-saveimage>`
except that output directory, suffix, and XML filenames are extracted from
the given "QUIP Operation File". In addition to output images, "QUIP Out" and
"QUIP Activity Log" are also saved, if applicable.

"""
# STDLIB
import os
import shutil

# GINGA
from ginga.gw import Widgets
from ginga.rv.plugins.SaveImage import SaveImage as SaveImageParent
from ginga.util.iohelper import shorten_name

# LOCAL
from wss_tools.quip.main import QUIP_DIRECTIVE, QUIP_LOG
from wss_tools.quip.qio import quip_out_dict
from wss_tools.utils.io import output_xml

__all__ = ['SaveQUIP']


# This uses SaveImage settings but have to be named differently to avoid
# name confusion in Ginga.
class SaveQUIP(SaveImageParent):

    def __init__(self, fv):
        super(SaveQUIP, self).__init__(fv)

        # Get output directories and XML filenames from QUIP
        self.outdir = QUIP_DIRECTIVE['OUTPUT']['OUTPUT_DIRECTORY']
        self.logfile = QUIP_DIRECTIVE['OUTPUT']['LOG_FILE_PATH']
        self.stafile = QUIP_DIRECTIVE['OUTPUT']['OUT_FILE_PATH']

        # Ensure output directory exists
        if not os.path.exists(self.outdir):
            os.makedirs(self.outdir)

    def build_gui(self, container):
        """Build GUI such that image list area is maximized."""

        vbox, sw, orientation = Widgets.get_oriented_box(container)

        captions = (('Channel:', 'label', 'Channel Name', 'combobox',
                     'Modified only', 'checkbutton'), )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)

        b.channel_name.set_tooltip('Channel for locating images to save')
        b.channel_name.add_callback('activated', self.select_channel_cb)

        mod_only = self.settings.get('modified_only', True)
        b.modified_only.set_state(mod_only)
        b.modified_only.add_callback('activated', lambda *args: self.redo())
        b.modified_only.set_tooltip("Show only locally modified images")

        container.add_widget(w, stretch=0)

        captions = (('Path:', 'llabel', 'OutDir', 'entry'),
                    ('Log:', 'llabel', 'LogFile', 'entry'),
                    ('Out:', 'llabel', 'StaFile', 'entry'),
                    ('Suffix:', 'llabel', 'Suffix', 'entry'))
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)

        b.outdir.set_text(self.outdir)
        b.outdir.set_tooltip('Output directory')

        b.logfile.set_text(self.logfile)
        b.logfile.set_tooltip('QUIP Log')

        b.stafile.set_text(self.stafile)
        b.stafile.set_tooltip('QUIP Out')

        b.suffix.set_text(self.suffix)
        b.suffix.set_tooltip('Suffix to append to filename')
        b.suffix.add_callback('activated', lambda w: self.set_suffix())

        container.add_widget(w, stretch=0)

        self.treeview = Widgets.TreeView(auto_expand=True,
                                         sortable=True,
                                         selection='multiple',
                                         use_alt_row_color=True)
        self.treeview.setup_table(self.columns, 1, 'IMAGE')
        self.treeview.add_callback('selected', self.toggle_save_cb)
        container.add_widget(self.treeview, stretch=1)

        captions = (('Status', 'llabel'), )
        w, b = Widgets.build_info(captions, orientation=orientation)
        self.w.update(b)
        b.status.set_text('')
        b.status.set_tooltip('Status message')
        container.add_widget(w, stretch=0)

        btns = Widgets.HBox()
        btns.set_border_width(4)
        btns.set_spacing(3)

        btn = Widgets.Button('Save')
        btn.set_tooltip('Save selected image(s)')
        btn.add_callback('activated', lambda w: self.save_images())
        btn.set_enabled(False)
        btns.add_widget(btn, stretch=0)
        self.w.save = btn

        btn = Widgets.Button('Close')
        btn.add_callback('activated', lambda w: self.close())
        btns.add_widget(btn, stretch=0)
        btn = Widgets.Button("Help")
        btn.add_callback('activated', lambda w: self.help())
        btns.add_widget(btn, stretch=0)
        btns.add_widget(Widgets.Label(''), stretch=1)
        container.add_widget(btns, stretch=0)

        self.gui_up = True

        # Generate initial listing
        try:
            self.update_channels()
        except ValueError:  # This fails if plugin autoloads on startup
            pass

    def _write_quiplog(self):
        """Write QUIP XML logfile using ChangeHistory entries.
        """
        channel = self.fv.get_channelInfo(self.chname)
        if channel is None:
            return

        history_plgname = 'ChangeHistory'
        try:
            history_obj = self.fv.gpmon.getPlugin(history_plgname)
        except Exception:
            self.logger.error(
                f'{history_plgname} plugin is not loaded. '
                f'No {self.logfile} will be written.')
            return

        if channel.name not in history_obj.name_dict:
            self.logger.error(
                f'{channel.name} channel not found in {history_plgname}. '
                f'No {self.logfile} will be written.')
            return

        file_dict = history_obj.name_dict[channel.name]

        # Insert change history into QUIP log, ordered by image name,
        # and then by timestamp.
        for imname in sorted(file_dict):
            entries = file_dict[imname]
            for timestamp in sorted(entries):
                bnch = entries[timestamp]
                date_str, time_str = timestamp.split(' ')
                QUIP_LOG.add_entry(date_str, time_str, imname, imname,
                                   bnch.DESCRIP, 'status')

        output_xml(QUIP_LOG.xml_dict(), self.logfile)

    def save_images(self):
        """Save selected images and output XML files.
        """
        output_images = []

        res_dict = self.treeview.get_selected()
        clobber = self.settings.get('clobber', False)
        self.treeview.clear_selection()  # Automatically disables Save button

        # If user gives empty string, no suffix.
        if self.suffix:
            sfx = '_' + self.suffix
        else:
            sfx = ''

        # Also include channel name in suffix. This is useful if user likes to
        # open the same image in multiple channels.
        if self.settings.get('include_chname', True):
            sfx += '_' + self.chname

        # Process each selected file. Each can have multiple edited extensions.
        for infile in res_dict:
            f_pfx = os.path.splitext(infile)[0]  # prefix
            f_ext = '.fits'  # Only FITS supported
            oname = f_pfx + sfx + f_ext
            outfile = os.path.join(self.outdir, oname)

            self.w.status.set_text(
                f'Writing out {shorten_name(infile, 10)} to '
                f'{shorten_name(oname, 10)} ...')
            self.logger.debug(
                f'Writing out {infile} to {oname} ...')

            if os.path.exists(outfile) and not clobber:
                self.logger.error(f'{outfile} already exists')
                continue

            bnch = res_dict[infile]

            if bnch.path is None or not os.path.isfile(bnch.path):
                self._write_mosaic(f_pfx, outfile)
            else:
                shutil.copyfile(bnch.path, outfile)
                self._write_mef(f_pfx, bnch.extlist, outfile)

            output_images.append(outfile)
            self.logger.info(f'{outfile} written')

        # Save QUIP Log, which stores change history
        self.logger.info(f'Saving {self.logfile}')
        try:
            self._write_quiplog()
        except Exception as e:
            self.w.status.set_text('Cannot write QUIP log!')
            self.logger.error(str(e))
            return

        # Save QUIP Out, which stores output image list
        self.logger.info(f'Saving {self.stafile}')
        try:
            output_xml(quip_out_dict(images=output_images), self.stafile)
        except Exception as e:
            self.w.status.set_text('Cannot write QUIP out!')
            self.logger.error(str(e))
            return

        self.w.status.set_text('Done! Quit Ginga to exit QUIP')

    def __str__(self):
        return 'savequip'


# Append module docstring with config doc for auto insert by Sphinx.
from ginga.util.toolbox import generate_cfg_example  # noqa
if __doc__ is not None:
    __doc__ += generate_cfg_example(
        'plugin_SaveImage', cfgpath='config', package='wss_tools.quip')
