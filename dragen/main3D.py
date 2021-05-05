import os
import sys
import datetime
import numpy as np

import logging
import logging.handlers
import pandas as pd

from dragen.generation.DiscreteRsa3D import DiscreteRsa3D
from dragen.generation.DiscreteTesselation3D import Tesselation3D
from dragen.utilities.RVE_Utils import RVEUtils
from dragen.generation.mesher import Mesher
from dragen.postprocessing.voldistribution import PostProcVol


class DataTask3D(RVEUtils):

    def __init__(self, box_size: int, n_pts: int, number_of_bands: int, bandwidth: float, shrink_factor: float = 0.5,
                 band_ratio_rsa: float = 0.95, band_ratio_final: float = 0.95, file1=None, file2=None, store_path=None,
                 gui_flag=False, anim_flag=False, gan_flag=False, exe_flag=False):

        self.logger = logging.getLogger("RVE-Gen")
        self.box_size = box_size
        self.n_pts = n_pts  # has to be even
        self.bin_size = self.box_size / self.n_pts
        self.step_half = self.bin_size / 2
        self.number_of_bands = number_of_bands
        self.bandwidth = bandwidth
        self.shrink_factor = float(np.cbrt(shrink_factor))
        self.band_ratio_rsa = band_ratio_rsa            # Band Ratio for RSA
        self.band_ratio_final = band_ratio_final        # Band ratio for Tesselator - final is br1 * br2
        self.gui_flag = gui_flag
        self.gan_flag = gan_flag
        self.root_dir = './'
        self.store_path = None
        self.fig_path = None
        self.gen_path = None

        if exe_flag:
            self.root_dir = store_path
        if not gui_flag:
            self.root_dir = sys.argv[0][:-14]  # setting root_dir to root_dir by checking path of current file
        elif gui_flag and not exe_flag:
            self.root_dir = store_path

        self.logger.info('the exe_flag is: ' + str(exe_flag))
        self.logger.info('root was set to: ' + self.root_dir)
        self.animation = anim_flag
        self.file1 = file1
        self.file2 = file2

        self.x_grid, self.y_grid, self.z_grid = super().gen_grid()

        super().__init__(box_size, n_pts, self.x_grid, self.y_grid, self.z_grid, bandwidth, debug=False)

    def setup_logging(self):
        LOGS_DIR = self.root_dir + '/Logs/'
        if not os.path.isdir(LOGS_DIR):
            os.makedirs(LOGS_DIR)
        f_handler = logging.handlers.TimedRotatingFileHandler(
            filename=os.path.join(LOGS_DIR, 'dragen-logs'), when='midnight')
        formatter = logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s')
        f_handler.setFormatter(formatter)
        self.logger.addHandler(f_handler)
        self.logger.setLevel(level=logging.DEBUG)

    def initializations(self, dimension, epoch):

        self.setup_logging()

        phase1_csv = self.file1
        phase2_csv = self.file2

        self.logger.info("RVE generation process has started...")
        phase1_df = super().read_input(phase1_csv, dimension)
        phase1_df['phaseID'] = 1
        grains_df = phase1_df.copy()

        if phase2_csv is not None:
            phase2_df = super().read_input(phase2_csv, dimension)
            phase1_df['phaseID'] = 2
            grains_df = pd.concat([grains_df, phase2_df])

        grains_df = super().process_df(grains_df, self.shrink_factor)

        total_volume = sum(grains_df['final_conti_volume'].values)
        estimated_boxsize = np.cbrt(total_volume)
        self.logger.info("the total volume of your dataframe is {}. A boxsize of {} is recommended.".
                         format(total_volume, estimated_boxsize))

        self.store_path = self.root_dir + '/OutputData/' + str(datetime.datetime.now())[:10] + '_' + str(epoch)
        self.fig_path = self.store_path + '/Figs'
        self.gen_path = self.store_path + '/Generation_Data'

        if not os.path.isdir(self.store_path):
            os.makedirs(self.store_path)
        if not os.path.isdir(self.fig_path):
            os.makedirs(self.fig_path)  # Second if needed
        if not os.path.isdir(self.gen_path):
            os.makedirs(self.gen_path)  # Second if needed

        grains_df.to_csv(self.gen_path+'/grain_data.csv', index=False)
        grains_df['final_conti_volume'].to_csv(self.gen_path+'/conti_input_vol.csv', index=False)
        grains_df['final_discrete_volume'].to_csv(self.gen_path + '/discrete_input_vol.csv', index=False)

        return grains_df, self.store_path

    def rve_generation(self, grains_df, store_path) -> str:

        discrete_RSA_obj = DiscreteRsa3D(self.box_size, self.n_pts,
                                         grains_df['a'].tolist(),
                                         grains_df['b'].tolist(),
                                         grains_df['c'].tolist(),
                                         grains_df['alpha'].tolist(), store_path=store_path)

        if self.number_of_bands > 0:
            # initialize empty grid_array for bands called band_array


            band_array = super().gen_array()
            band_array = super().gen_boundaries_3D(band_array)

            for i in range(self.number_of_bands):
                band_array = super().band_generator(band_array)

            rsa, x_0_list, y_0_list, z_0_list, rsa_status = discrete_RSA_obj.run_rsa(self.band_ratio_rsa, band_array,
                                                                                     animation=self.animation)
            grains_df['x_0'] = x_0_list
            grains_df['y_0'] = y_0_list
            grains_df['z_0'] = z_0_list

        else:
            rsa, x_0_list, y_0_list, z_0_list, rsa_status = discrete_RSA_obj.run_rsa(animation=self.animation)
            grains_df['x_0'] = x_0_list
            grains_df['y_0'] = y_0_list
            grains_df['z_0'] = z_0_list

        if rsa_status:
            discrete_tesselation_obj = Tesselation3D(self.box_size, self.n_pts, grains_df,
                                                     self.shrink_factor, self.band_ratio_final, store_path)
            rve, rve_status = discrete_tesselation_obj.run_tesselation(rsa, animation=self.animation)

        else:
            self.logger.info("The rsa did not succeed...")
            sys.exit()

        if rve_status:
            periodic_rve_df = super().repair_periodicity_3D(rve)
            periodic_rve_df['phaseID'] = 0
            # An den NaN-Werten in dem DF liegt es nicht!

            grains_df.sort_values(by=['GrainID'])

            for i in range(len(grains_df)):
                # Set grain-ID to number of the grain
                # Denn Grain-ID ist entweder >0 oder -200 oder >-200
                periodic_rve_df.loc[periodic_rve_df['GrainID'] == i + 1, 'phaseID'] = grains_df['phaseID'][i]

            if self.number_of_bands > 0:
                # Set the points where == -200 to phase 2 and to grain ID i + 2
                periodic_rve_df.loc[periodic_rve_df['GrainID'] == -200, 'GrainID'] = (i + 2)
                periodic_rve_df.loc[periodic_rve_df['GrainID'] == (i + 2), 'phaseID'] = 2

            # Start the Mesher
            mesher_obj = Mesher(periodic_rve_df, grains_df, store_path=store_path,
                                phase_two_isotropic=True, animation=False)
            mesher_obj.mesh_and_build_abaqus_model()

        self.logger.info("RVE generation process has successfully completed...")
        PostProcVol(store_path, dim_flag=3).gen_plots()

        return store_path



