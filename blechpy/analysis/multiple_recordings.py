from blechpy.dio import h5io
from blechpy.widgets import userIO
import pandas as pd
import numpy as np
import pylab as plt
import easygui as eg
import os
import tables
from sklearn.decomposition import PCA
from scipy.spatial.distance import cdist
import seaborn as sns
sns.set(style='white', context='talk', font_scale=2)


def get_next_letter(letter):
    '''gets next letter in the alphabet

    Parameters
    ----------
    letter : str
    '''
    return bytes([bytes(letter, 'utf-8')[0] + 1]).decode('utf-8')


def calc_J1(wf_day1, wf_day2):
    # Get the mean PCA waveforms on days 1 and 2
    day1_mean = np.mean(wf_day1, axis=0)
    day2_mean = np.mean(wf_day2, axis=0)

    # Get the Euclidean distances of each day from its daily mean
    day1_dists = cdist(wf_day1, day1_mean.reshape((-1, 3)), metric='euclidean')
    day2_dists = cdist(wf_day2, day2_mean.reshape((-1, 3)), metric='euclidean')

    # Sum up the distances to get J1
    J1 = np.sum(day1_dists) + np.sum(day2_dists)
    return J1


def calc_J2(wf_day1, wf_day2):
    # Get the mean PCA waveforms on days 1 and 2
    day1_mean = np.mean(wf_day1, axis=0)
    day2_mean = np.mean(wf_day2, axis=0)

    # Get the overall inter-day mean
    overall_mean = np.mean(np.concatenate((wf_day1, wf_day2), axis=0), axis=0)

    # Get the distances of the daily means from the inter-day mean
    dist1 = cdist(day1_mean.reshape((-1, 3)), overall_mean.reshape((-1, 3)))
    dist2 = cdist(day2_mean.reshape((-1, 3)), overall_mean.reshape((-1, 3)))

    # Multiply the distances by the number of points on both days and sum to
    # get J2
    J2 = wf_day1.shape[0]*np.sum(dist1) + wf_day2.shape[0]*np.sum(dist2)
    return J2


def calc_J3(wf_day1, wf_day2):
    '''Calculate J3 value between 2 sets of PCA waveforms

    Parameters
    ----------
    wf_day1 : numpy.array
        PCA waveforms for a single unit from session 1
    wf_day2 : numpy.array
        PCA waveforms for a single unit from session 2

    Returns
    -------
    J3 : float
    '''
    J1 = calc_J1(wf_day1, wf_day2)
    J2 = calc_J2(wf_day1, wf_day2)
    J3 = J2 / J1
    return J3


def get_intra_J3(rec_dirs):
    # Go through each recording directory and compute intra_J3 array
    for rd in rec_dirs:
        h5_name = h5io.get_h5_filename(rd)
        h5_file = os.path.join(rd, h5_name)

        with tables.open_file(h5_file, 'r') as hf5:
            for node, descrip in zip(hf5.root.sorted_units,
                                     hf5.unit_descriptor.iterrows()):
                if descrip['single_unit'] == 1:
                    waves = node.waveforms[:]
                    pca = PCA(n_components=3)
                    pca.fit(waves)
                    pca_waves = pca.transform(waves)
                    idx1 = int(waves.shape[0] * (1.0 / 3.0))
                    idx2 = int(waves.shape[0] * (2.0 / 3.0))
                    tmp_J3 = calc_J3(pca_waves[:idx1, :],
                                     pca_waves[idx2:, :])
                    intra_J3.append(tmp_J3)


def find_held_units(rec_dirs, intra_J3, percent_criterion):
    rec_pairs = [(rec_dirs[i], rec_dirs[i+1])
                 for i in range(len(rec_dirs)-1)]

    held_df = pd.DataFrame(columns=['unit',
                                    *[os.path.basename(x) for x in rec_dirs]])

    # Go through each pair of directories and computer inter_J3 between
    # units. If the inter_J3 values is below the percentile_criterion of
    # the intra_j3 array then mark units as held. Only compare the same
    # type of single units on the same electrode
    inter_J3 = []
    for rd1, rd2 in rec_pairs:
        h5_file1 = os.path.join(rd1,
                                h5io.get_h5_filename(rd1))
        h5_file2 = os.path.join(rd2,
                                h5io.get_h5_filename(rd2))
        rec1 = os.path.basename(rd1)
        rec2 = os.path.basename(rd2)

        with tables.open_file(h5_file1, 'r') as hf51, \
                tables.open_file(h5_fiel2, 'r') as hf52:

            for node1, descrip1 in zip(
                                   hf51.root.sorted_units,
                                   hf51.root.unit_descriptor.iterrows()):
                if descrip1['single_unit'] == 1:
                    for node2, descrip2 in zip(
                            hf52.root.sorted_units,
                            hf52.root.unit_descriptor.iterrows()):
                        if descrip1 == descrip2:

                            wf1 = node1.waveforms[:]
                            wf2 = node2.waveforms[:]

                            pca = PCA(n_components=3)
                            pca.fit(np.concatenate((wf1, wf2), axis=0))
                            pca_wf1 = pca.transform(wf1)
                            pca_wf2 = pca.transform(wf2)

                            J3 = calc_J3(pca_wf1, pca_wf2)
                            inter_J3.append(J3)

                            if J3 <= np.percentile(intra_J3,
                                                   percent_criterion):
                                # Add unit to proper spot in Dataframe
                                unit1 = node1._v_name
                                unit2 = node2._v_name
                                if held_df.empty:
                                    held_df = \
                                        held_df.append({'unit': 'A',
                                                        rec1: unit1,
                                                        rec2: unit2},
                                                       ignore_index=True)
                                    continue

                                idx1 = np.where(held_df[rec1] == unit1)[0]
                                idx2 = np.where(held_df[rec2] == unit2)[0]

                                if idx1.size == 0 and idx2.size == 0:
                                    uL = held_df['unit'].iloc[-1]
                                    uL = get_next_letter(uL)
                                    tmp = {'unit': uL,
                                           rec1: unit1,
                                           rec2: unit2}
                                    held_df = held_df.append(
                                        tmp,
                                        ignore_index=True)
                                elif idx1.size != 0 and idx2.size != 0:
                                    continue
                                elif idx1.size != 0:
                                    held_df[rec2].iloc[idx1[0]] = unit2
                                else:
                                    held_df[rec1].iloc[idx2[0]] = unit1
    return held_df, inter_J3


class multi_dataset(object):

    def __init__(self, exp_dir=None, shell=False):
        '''Setup for analysis across recording sessions

        Parameters
        ----------
        exp_dir : str (optional)
            path to directory containing all recording directories
            if None (default) is passed then a popup to choose file
            will come up
        shell : bool (optional)
            True to use command-line interface for user input
            False (default) for GUI
        '''
        if exp_dir is None:
            exp_dir = eg.diropenbox('Select Experiment Directory',
                                    'Experiment Directory')
            if exp_dir is None or exp_dir == '':
                return

        file_dirs = [x for x in os.listdir(exp_dir) if os.path.isdir(x)]
        order_dict = dict.fromkeys(file_dirs, 0)
        tmp = userIO.dictIO(order_dict, shell=shell)
        order_dict = tmp.fill_dict(prompt=('Set order of recordings (1-%i)\n'
                                           'Leave blank to delete directory'
                                           ' from list'))
        if order_dict is None:
            return

        file_dirs = [k for k, v in order_dict.items()
                     if v is not None and v != 0]
        file_dirs = sorted(file_dirs, key=order_dict.get)
        file_dirs = [os.path.join(exp_dir, x) for x in file_dirs]
        self.recording_dirs = file_dirs
        self.experiment_dir = exp_dir

    def _order_dirs(self, shell=None):
        '''set order of redcording directories
        '''
        if shell is None:
            shell = self.shell
        self.recording_dirs = [x[:-1] if x.endswith('/') else x
                               for x in self.recording_dirs]
        top_dirs = {os.path.basename(x): os.path.dirname(x)
                    for x in self.recording_dirs}
        file_dirs = list(top_dirs.keys())
        order_dict = dict.fromkeys(file_dirs, 0)
        tmp = userIO.dictIO(order_dict, shell=shell)
        order_dict = tmp.fill_dict(prompt=('Set order of recordings (1-%i)\n'
                                           'Leave blank to delete directory'
                                           ' from list'))
        if order_dict is None:
            return

        file_dirs = [k for k, v in order_dict.items()
                     if v is not None and v != 0]
        file_dirs = sorted(file_dirs, key=order_dict.get)
        file_dirs = [os.path.join(top_dirs.get(x), x) for x in file_dirs]
        self.recording_dirs = file_dirs

    def _add_dir(self, new_dir=None, shell=None):
        '''Add recording directory to experiment

        Parameters
        ----------
        new_dir : str (optional)
            full path to new directory to add to recording dirs
        shell : bool (optional)
            True for command-line interface for user input
            False (default) for GUI
            If not passed then the preference set upon object creation is used
        '''
        if shell is None:
            shell = self.shell

        if new_dir is None:
            if shell:
                new_dir = input('Full path to new directory:  ')
            else:
                new_dir = eg.diropenbox('Select new recording directory',
                                        'Add Recording Directory')

        if os.path.isdir(new_dir):
            self.recording_dirs.append(new_dir)
        else:
            raise NotADirectoryError('new directory must be a valid full'
                                     ' path to a directory')

        self._order_dirs(shell=shell)

    def held_units_detect(self, percent_criterion=5, shell=None):
        '''Determine which units are held across recording sessions
        Grabs single units from each recording and compares consecutive
        recordings to determine if units were held

        Parameters
        ----------
        percent_criterion : float
            percentile (0-100) of intra_J3 below which to accept units as held
            5.0 (default) for 95th percentile
            lower number is stricter criteria

        shell : bool (optional)
            True for command-line interface for user input
            False (default) for GUI
            If not given the shell preference set upon object creation is used
        '''
        save_dir = os.path.join(self.experiment_dir, 'held_units')
        if not os.path.exists(save_dir):
            os.mkdir(save_dir)

        rec_dirs = self.recording_dirs
        rec_dirs = [x[:-1] if x.endswith('/') else x
                    for x in self.recording_dirs]

        intra_J3 = get_intra_J3(rec_dirs)
        held_df, inter_J3 = find_held_units(rec_dirs,
                                            intra_J3,
                                            percent_criterion)

        self.held_units = {'units': held_df,
                           'intra_J3': intra_J3,
                           'inter_J3': inter_J3}

        # Write dataframe of held units to text file
        # For each held unit, plot waveforms side by side
        # Plot intra and inter J3

