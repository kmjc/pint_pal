"""
This code contains the TimingConfiguration class, which is used to load configuration files and perform
actions, with items then passed to the notebooks.

Very basic usage:
    from timingconfiguration import TimingConfiguration
    tc = TimingConfiguration(CONFIGFILE)
"""
import io
import os
import pint.toa as toa
import pint.models as model
import pint.fitter
import numpy as np
import astropy.units as u
from astropy import log
import yaml
from timing_analysis.utils import write_if_changed, apply_cut_flag, apply_cut_select
from timing_analysis.lite_utils import new_changelog_entry
from timing_analysis.defaults import *

class TimingConfiguration:
    """
    This class contains the functionality to read
    from a configuration file and send that information
    to the timing notebooks.
    """
    def __init__(self, filename="config.yaml", tim_directory=None, par_directory=None):
        """
        Initialization method.

        Normally config files are written to be run from the root of a
        git checkout on the NANOGrav notebook server. If you want to run
        them from somewhere else, you may need to override these directories
        when you construct the TimingConfiguration object; this will not
        change what is recorded in the config file.

        Parameters
        ==========
        filename (optional) : path to the configuration file
        tim_directory (optional) : override the tim directory specified in the config
        par_directory (optional) : override the par directory specified in the config
        """
        self.filename = filename
        with open(filename) as FILE:
            self.config = yaml.load(FILE, Loader=yaml.FullLoader)
        self.tim_directory = self.config['tim-directory'] if tim_directory is None else tim_directory
        self.par_directory = self.config['par-directory'] if par_directory is None else par_directory
        self.skip_check = self.config['skip-check'] if 'skip-check' in self.config.keys() else ''

    def get_source(self):
        """ Return the source name """
        return self.config['source']

    def get_compare_model(self):
        """ Return the timing model file to compare with """
        if "compare-model" in self.config.keys() and self.config['compare-model'] is not None:
            return os.path.join(self.par_directory, self.config['compare-model'])
        return None

    def get_free_params(self, fitter):
        """Return list of free parameters"""
        if self.config["free-dmx"]:
            return self.config['free-params'] + [p for p in fitter.model.params if p.startswith("DMX_")]
        else:
            return self.config['free-params']

    def get_model_and_toas(self,usepickle=True,print_all_ignores=False,apply_initial_cuts=True):
        """Return the PINT model and TOA objects"""
        par_path = os.path.join(self.par_directory,self.config["timing-model"])
        toas = self.config["toas"]

        # Individual tim file
        if isinstance(toas, str):
            toas = [toas]

        BIPM = self.get_bipm()
        EPHEM = self.get_ephem()
        m = model.get_model(par_path)

        if m.PSR.value != self.get_source():
            log.warning(f'{self.filename} source entry does not match par file value ({m.PSR.value}).')

        picklefilename = os.path.basename(self.filename) + ".pickle.gz"
        # Merge toa_objects (check this works for list of length 1)
        t = toa.get_TOAs([os.path.join(self.tim_directory,t) for t in toas],
                          usepickle=usepickle,
                          bipm_version=BIPM,
                          ephem=EPHEM,
                          planets=PLANET_SHAPIRO,
                          model=m,
                          picklefilename=picklefilename)

        # if we're dealing with wideband TOAs, each epoch has a single TOA, 
        # so don't bother checking to see if we can reduce entries
        if self.get_toa_type() == "NB":
            self.check_for_bad_epochs(t, threshold=0.9, print_all=print_all_ignores)

        # Make a clean copy of original TOAs table (to track cut TOAs, flag_values)
        t.orig_table = t.table.copy()

        # Add 'cut' flags to TOAs according to config 'ignore' block.
        if apply_initial_cuts:
            self.check_for_orphaned_recs(t)
            #t = self.apply_ignore(t,specify_keys=['mjd-start','mjd-end','snr-cut','bad-range'])
            #apply_cut_select(t,reason='initial cuts - ignore block')

        return m, t

    def get_bipm(self):
        """ Return the bipm string """
        if "bipm" in self.config.keys():
            return self.config['bipm']
        return None #return some default value instead?

    def get_ephem(self):
        """ Return the ephemeris string """
        if "ephem" in self.config.keys():
            return self.config['ephem']
        return None #return some default value instead?

    def print_changelog(self):
        """Print changelog entries from .yaml in the notebook."""
        # If there's a changelog, write out its contents. If not, complain.
        if 'changelog' in self.config.keys():
            print('changelog:')
            if self.config['changelog'] is not None:
                for cl in self.config['changelog']:
                    print(f'  - {cl}')
            else:
                print('...no changelog entries currently exist.')
        else:
            print('YAML file does not include a changelog. Add \'changelog:\' and individual entries there.')

    def get_fitter(self):
        """ Return the fitter string (do more?) """
        if "fitter" in self.config.keys():
            return self.config['fitter']
        return None

    def construct_fitter(self, to, mo):
        """ Return the fitter, tracking pulse numbers if available """
        fitter_name = self.config['fitter']
        fitter_class = getattr(pint.fitter, fitter_name)
        return fitter_class(to, mo)


    def get_toa_type(self):
        """ Return the toa-type string """
        if "toa-type" in self.config.keys():
            return self.config['toa-type']
        return None

    def get_outfile_basename(self,ext=''):
        """ Return source.[nw]b basename (e.g. J1234+5678.nb) """
        basename = f'{self.get_source()}.{self.get_toa_type().lower()}'
        if ext: basename = '.'.join([basename,ext])
        return basename

    def get_niter(self):
        """ Return an integer of the number of iterations to fit """
        if "n-iterations" in self.config.keys():
            return int(self.config['n-iterations'])
        return 1

    def get_mjd_start(self):
        """Return mjd-start quantity (applies units days)"""
        if 'mjd-start' in self.config['ignore'].keys():
            return self.config['ignore']['mjd-start']
        return None

    def get_mjd_end(self):
        """Return mjd-end quantity (applies units days)"""
        if 'mjd-end' in self.config['ignore'].keys():
            return self.config['ignore']['mjd-end']
        return None

    def get_orphaned_rec(self):
        """Return orphaned receiver(s)"""
        if 'orphaned-rec' in self.config['ignore'].keys():
            return self.config['ignore']['orphaned-rec']
        return None 

    def get_snr_cut(self):
        """ Return value of the TOA S/N cut """
        if "snr-cut" in self.config['ignore'].keys():
            return self.config['ignore']['snr-cut']
        return None #return some default value instead?

    def get_bad_epochs(self):
        """ Return list of bad epochs (basenames: [backend]_[mjd]_[source]) """
        if 'bad-epoch' in self.config['ignore'].keys():
            return self.config['ignore']['bad-epoch']
        return None

    def get_bad_ranges(self):
        """ Return list of bad epoch ranges by MJD ([MJD1,MJD2])"""
        if 'bad-range' in self.config['ignore'].keys():
            return self.config['ignore']['bad-range']
        return None

    def get_bad_toas(self):
        """ Return list of bad TOAs (lists: [filename, channel, subint]) """
        if 'bad-toa' in self.config['ignore'].keys():
            return self.config['ignore']['bad-toa']
        return None

    def check_for_orphaned_recs(self, toas, nepochs_threshold=3):
        """Check for frontend/backend pairs that arise at or below threshold
        for number of epochs; return list of those that are orphaned.
        
        Parameters
        ==========
        toas: `pint.TOAs object`
        nepochs_threshold: int, optional
            Number of epochs at/below which a frontend/backend pair is orphaned.

        Returns
        =======
        febe_to_cut: list
            List of orphaned receivers to cut.
        """
        febe_pairs = set(toas.get_flag_value('f')[0])
        log.info(f'Frontend/backend pairs present in this data set: {febe_pairs}')

        febe_to_cut = []
        for febe in febe_pairs:
            f_bool = np.array([f == febe for f in toas.get_flag_value('f')[0]])
            f_names = toas[f_bool].get_flag_value('name')[0]
            epochs = set(f_names)
            n_epochs = len(epochs)
            if n_epochs > nepochs_threshold:
                log.info(f'{febe} epochs: {n_epochs}')
            else:
                febe_to_cut.append(febe)

        if febe_to_cut and ('orphaned-rec' in self.config['ignore'].keys()):
            ftc = set(febe_to_cut)
            if not self.get_orphaned_rec(): orph = set()
            else: orph = set(self.get_orphaned_rec())
            # Do sets of receivers to cut and those listed in the yaml match?
            if not (ftc == orph):
                # Add/remove from orphaned-rec?
                if (ftc - orph):
                    log.warning(f"{nepochs_threshold} or fewer epochs, add to orphaned-rec: {', '.join(ftc-orph)}")
                elif (orph - ftc):
                    log.warning(f"Remove from orphaned-rec: {', '.join(orph-ftc)}")
            else:
                pass     
        elif febe_to_cut:  # ...but no orphaned-rec field in the ignore block.
            febe_cut_str = ', '.join(febe_to_cut)
            log.warning(f"Add orphaned-rec to the ignore block in {self.filename}.")
            log.warning(f"{nepochs_threshold} or fewer epochs, add to orphaned-rec: {febe_cut_str}")
            print(f"Add the following line to {self.filename}...")
            new_changelog_entry('CURATE',f"orphaned receivers ({nepochs_threshold} or fewer epochs): {febe_cut_str}")

        return febe_to_cut

    def check_for_bad_epochs(self, toas, threshold=0.9, print_all=False):
        """Check the bad-toas entries for epochs where more than a given
        percentange of TOAs have been flagged. Make appropriate suggestions
        for the user to update the `bad-epoch` entires, and optionally
        supply the revised `bad-toa` entires.

        Parameters
        ----------
        toas: pint.TOA
            A PINT TOA object that contains a table of TOAs loaded

        threshold: float
            A threshold fraction used to determine whether to suggest adding
            a bad-epoch line to the config file. Should be in the range [0, 1].
            Default is 0.9.

        print_all: bool
            If True, print both the suggested bad-epoch lines AND the revised
            bad-toa lines, where the new bad-toa lines now have entries from
            the suggested bad-epochs removed. Default is False.
        """
        # get the list of bad-toas already in the config file
        # only continue if that list has entries
        provided_bad_toas = self.get_bad_toas()
        if isinstance(provided_bad_toas, list):
            bad_toa_epochs = np.asarray(provided_bad_toas)[:, 0]

            # how many bad TOAs per epoch?
            unique, counts = np.unique(bad_toa_epochs, return_counts=True)
            bad_toa_epoch_counts = dict(zip(unique, counts))

            # how many raw TOAs per epoch?
            toa_epochs = toas.get_flag_value("name")[0]
            unique, counts = np.unique(toa_epochs, return_counts=True)
            toa_epoch_counts = dict(zip(unique, counts))

            # get the list of bad-epochs already in the config
            provided_bad_epochs = self.get_bad_epochs()
            if not isinstance(provided_bad_epochs, list):
                provided_bad_epochs = []

            # are there any epochs that have too many bad TOAs?
            new_bad_epochs = []
            for k in bad_toa_epoch_counts:
                # at this point, TOAs could have already been removed,
                # so check that the key exists first
                if k in toa_epoch_counts.keys():
                    n_toas = toa_epoch_counts[k]
                    n_bad = bad_toa_epoch_counts[k]
                    bad_frac = float(n_bad) / n_toas
                    # check that the bad fraction exceeds the threshold
                    # AND that the current epoch isn't already listed
                    if bad_frac >= threshold and k not in provided_bad_epochs:
                        new_bad_epochs.append(k)

            # only bother printing anything if there's a suggestion
            if len(new_bad_epochs) > 0:
                log.warn(
                    f"More than {threshold * 100}% of TOAs have been excised for some epochs"
                )
                log.info("Consider adding the following to `bad-epoch` in your config file:")
                for e in new_bad_epochs:
                    print(f"    - '{e}'")

            # if requested to update the bad-toa lines, figure out which
            # entries need to be removed
            if print_all:
                all_bad_epochs = np.concatenate((new_bad_epochs, provided_bad_epochs))
                bad_toas_to_del = []
                for e in all_bad_epochs:
                    _idx = np.where(bad_toa_epochs == e)[0]
                    bad_toas_to_del.extend(_idx)
                new_bad_toa_list = np.delete(np.asarray(provided_bad_toas), bad_toas_to_del, 0)
                log.info("The `bad-toa` list in your config file can be reduced to:")
                for t in new_bad_toa_list:
                    print(f"    - ['{t[0]}',{t[1]},{t[2]}]")



    def get_prob_outlier(self):
        if "prob-outlier" in self.config['ignore'].keys():
            return self.config['ignore']['prob-outlier']
        return None #return some default value instead?

    def get_noise_dir(self):
        """ Return base directory for noise results """
        if 'results-dir' in self.config['noise'].keys():
            return self.config['noise']['results-dir']
        return None

    def get_ignore_dmx(self):
        """ Return ignore-dmx toggle """
        if 'ignore-dmx' in self.config['dmx'].keys():
            return self.config['dmx']['ignore-dmx']
        return None

    def get_fratio(self):
        """ Return desired frequency ratio """
        if 'fratio' in self.config['dmx'].keys():
            return self.config['dmx']['fratio']
        return FREQUENCY_RATIO

    def get_sw_delay(self):
        """ Return desired max(solar wind delay) threshold """
        if 'max-sw-delay' in self.config['dmx'].keys():
            return self.config['dmx']['max-sw-delay']
        return MAX_SOLARWIND_DELAY

    def get_custom_dmx(self):
        """ Return MJD/binning params for handling DM events, etc. """
        if 'custom-dmx' in self.config['dmx'].keys():
            return self.config['dmx']['custom-dmx']
        return None

    def apply_ignore(self,toas,specify_keys=None):
        """ Basic checks and return TOA excision info. """
        OPTIONAL_KEYS = ['mjd-start','mjd-end','snr-cut','bad-toa','bad-range','bad-epoch'] # prob-outlier, bad-ff
        EXISTING_KEYS = self.config['ignore'].keys()
        VALUED_KEYS = [k for k in EXISTING_KEYS if self.config['ignore'][k] is not None]

        # INFO?
        missing_valid = set(OPTIONAL_KEYS)-set(EXISTING_KEYS)
        if len(missing_valid):
            log.info(f'Valid TOA excision keys not present: {missing_valid}')

        invalid = set(EXISTING_KEYS) - set(OPTIONAL_KEYS)
        if len(invalid):
            log.warning(f'Invalid TOA excision keys present: {invalid}')

        valid_null = set(EXISTING_KEYS) - set(VALUED_KEYS) - invalid
        if len(valid_null):
            log.info(f'TOA excision keys included, but NOT in use: {valid_null}')

        valid_valued = set(VALUED_KEYS) - invalid
        if len(valid_valued):
            # Provide capability to add -cut flags based on specific ignore fields
            if specify_keys is not None:
                valid_valued = valid_valued & set(specify_keys)
                log.info(f'Specified TOA excision keys: {valid_valued}')
            else:
                log.info(f'Valid TOA excision keys in use: {valid_valued}')
                 
        # All info here about selecting various TOAs.
        # Select TOAs to cut, then use apply_cut_flag.
        if 'mjd-start' in valid_valued:
            mjds = np.array([m for m in toas.orig_table['mjd_float']])
            startinds = np.where(mjds < self.get_mjd_start())[0]
            apply_cut_flag(toas,startinds,'mjdstart')
        if 'mjd-end' in valid_valued:
            mjds = np.array([m for m in toas.orig_table['mjd_float']])
            endinds = np.where(mjds > self.get_mjd_end())[0]
            apply_cut_flag(toas,endinds,'mjdend')
        if 'snr-cut' in valid_valued:
            snrs = np.array([f['snr'] for f in toas.orig_table['flags']])
            snrinds = np.where(snrs < self.get_snr_cut())[0]
            apply_cut_flag(toas,snrinds,'snr')
            if self.get_snr_cut() > 8.0 and self.get_toa_type() == 'NB':
                log.warning('snr-cut should be set to 8; try excising TOAs using other methods.')
            if self.get_snr_cut() > 25.0 and self.get_toa_type() == 'WB':
                log.warning('snr-cut should be set to 25; try excising TOAs using other methods.')
        if 'prob-outlier' in valid_valued:
            pass
        if 'bad-ff' in valid_valued:
            pass
        if 'bad-epoch' in valid_valued:
            names = np.array([f['name'] for f in to.orig_table['flags']])
            for be in self.get_bad_epochs():
                epochinds = np.where([be in n for n in names])[0]
                apply_cut_flag(toas,epochinds,'badepoch')
        if 'bad-range' in valid_valued:
            mjds = np.array([m for m in toas.orig_table['mjd_float']])
            backends = np.array([f['be'] for f in toas.orig_table['flags']])
            for br in self.get_bad_ranges():
                if len(br) > 2:
                    rangeinds = np.where((mjds>br[0]) & (mjds<br[1]) & (backends==br[2]))[0]
                else:
                    rangeinds = np.where((mjds>br[0]) & (mjds<br[1]))[0]
                apply_cut_flag(toas,rangeinds,'badrange')
        if 'bad-toa' in valid_valued:
            names = np.array([f['name'] for f in toas.orig_table['flags']])
            chans = np.array([f['chan'] for f in toas.orig_table['flags']])
            subints = np.array([f['subint'] for f in toas.orig_table['flags']])
            for bt in self.get_bad_toas():
                name,chan,subint = bt
                if self.get_toa_type() == 'NB':
                    btind = np.where((names==name) & (chans==chan) & (subints==subint))[0]
                else:
                    # don't match based on -chan flags, since WB TOAs don't have them
                    btind = np.where((names==name) & (subints==subint))[0]
                apply_cut_flag(toas,btind,'badtoa')

        return toas
