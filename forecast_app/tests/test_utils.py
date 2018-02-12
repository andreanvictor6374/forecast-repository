import datetime
from pathlib import Path

import pymmwr
from django.test import TestCase

from forecast_app.models import Project, TimeZero
from forecast_app.models.forecast_model import ForecastModel
from forecast_app.tests.test_project import TEST_CONFIG_DICT
from utils.cdc import epi_week_filename_components, ensemble_epi_week_filename_components
from utils.mean_absolute_error import mean_absolute_error
from utils.utilities import cdc_csv_filename_components, is_date_in_season, season_start_year_for_date


EPI_YR_WK_TO_ACTUAL_WILI = {
    (2016, 51): 2.74084,
    (2016, 52): 3.36496,
    (2017, 1): 3.0963,
    (2017, 2): 3.08492,
    (2017, 3): 3.51496,
    (2017, 4): 3.8035,
    (2017, 5): 4.45059,
    (2017, 6): 5.07947,
}


# static mock function for delphi_wili_for_mmwr_year_week(). location_name is ignored
def mock_wili_for_epi_week_fcn(forecast_model, year, week, location_name):
    return EPI_YR_WK_TO_ACTUAL_WILI[(year, week)]


class UtilitiesTestCase(TestCase):
    """
    """


    @classmethod
    def setUpTestData(cls):
        cls.project = Project.objects.create(config_dict=TEST_CONFIG_DICT)
        cls.project.load_template(Path('forecast_app/tests/2016-2017_submission_template.csv'))

        cls.forecast_model = ForecastModel.objects.create(project=cls.project)

        # EW1-KoTstable-2017-01-17.csv -> EW1 in 2017:
        time_zero = TimeZero.objects.create(project=cls.project, timezero_date=(pymmwr.mmwr_week_to_date(2017, 1)))
        cls.forecast1 = cls.forecast_model.load_forecast(
            Path('forecast_app/tests/model_error/ensemble/EW1-KoTstable-2017-01-17.csv'), time_zero)

        # EW2-KoTstable-2017-01-23.csv -> EW2 in 2017:
        time_zero = TimeZero.objects.create(project=cls.project, timezero_date=(pymmwr.mmwr_week_to_date(2017, 2)))
        cls.forecast2 = cls.forecast_model.load_forecast(
            Path('forecast_app/tests/model_error/ensemble/EW2-KoTstable-2017-01-23.csv'), time_zero)

        # EW51-KoTstable-2017-01-03.csv -> EW51 in 2016:
        time_zero = TimeZero.objects.create(project=cls.project, timezero_date=(pymmwr.mmwr_week_to_date(2016, 51)))
        cls.forecast3 = cls.forecast_model.load_forecast(
            Path('forecast_app/tests/model_error/ensemble/EW51-KoTstable-2017-01-03.csv'), time_zero)

        # EW52-KoTstable-2017-01-09.csv -> EW52 in 2016:
        time_zero = TimeZero.objects.create(project=cls.project, timezero_date=(pymmwr.mmwr_week_to_date(2016, 52)))
        cls.forecast4 = cls.forecast_model.load_forecast(
            Path('forecast_app/tests/model_error/ensemble/EW52-KoTstable-2017-01-09.csv'), time_zero)


    def test_epi_week_filename_components(self):
        filename_components_tuples = (('EW1-KoTstable-2017-01-17.csv', (1, 'KoTstable', datetime.date(2017, 1, 17))),
                                      ('-KoTstable-2017-01-17.csv', None),
                                      ('EW1--2017-01-17.csv', None),
                                      ('EW1-KoTstable-2017-01-17.txt', None))
        for filename, components in filename_components_tuples:
            self.assertEqual(components, epi_week_filename_components(filename))


    def test_ensemble_epi_week_filename_components(self):
        filename_components_tuples = (('EW01-2011-CU_EAKFC_SEIRS.csv', (1, 2011, 'CU_EAKFC_SEIRS')),
                                      ('EW01-2011-CUBMA.csv', (1, 2011, 'CUBMA')),
                                      ('-2011-CUBMA.csv', None),
                                      ('EW01--2011-CUBMA.csv', None),
                                      ('EW01-CUBMA.csv', None),
                                      ('EW01-2011.csv', None),
                                      ('EW01-2011-CUBMA.txt', None))
        for filename, components in filename_components_tuples:
            self.assertEqual(components, ensemble_epi_week_filename_components(filename))


    def test_name_components_from_cdc_csv_filename(self):
        filename_to_exp_component_tuples = {
            '20170419-gam_lag1_tops3-20170516.cdc.csv':
                (datetime.date(2017, 4, 19), 'gam_lag1_tops3', datetime.date(2017, 5, 16)),
            '20161023-KoTstable-20161109.cdc.csv':
                (datetime.date(2016, 10, 23), 'KoTstable', datetime.date(2016, 11, 9)),
            '20161023-KoTstable.cdc.csv':
                (datetime.date(2016, 10, 23), 'KoTstable', None),

            '': None,
            '20170419-gam_lag1_tops3-20170516.csv': None,
            'gam_lag1_tops3-20170516.csv': None,
            '-gam_lag1_tops3-20170516.cdc.csv': None,
            '20170419--.cdc.csv': None,
            '20170419-.cdc.csv': None,
            '20170419.cdc.csv': None,
            '20170419-gam_lag1_tops3-.cdc.csv': None,
            '20170419-gam-lag1-tops3-20170516.cdc.csv': None,
            '20170419-gam/lag1*tops3-20170516.cdc.csv': None,
        }
        for cdc_csv_filename, exp_components in filename_to_exp_component_tuples.items():
            self.assertEqual(exp_components, cdc_csv_filename_components(cdc_csv_filename))


    def test_is_forecast_in_season(self):
        # test lab standard breakpoint: "2016/2017 season" starts with EW30-2016 and ends with EW29-2017
        date_season_exp_is_in = [
            (pymmwr.mmwr_week_to_date(2016, 29), 2016, False),
            (pymmwr.mmwr_week_to_date(2016, 30), 2016, True),
            (pymmwr.mmwr_week_to_date(2016, 52), 2016, True),
            (pymmwr.mmwr_week_to_date(2017, 1), 2016, True),
            (pymmwr.mmwr_week_to_date(2017, 29), 2016, True),
            (pymmwr.mmwr_week_to_date(2017, 30), 2016, False),
        ]
        for date, season_start_year, exp_is_in in date_season_exp_is_in:
            self.assertEquals(exp_is_in, is_date_in_season(date, season_start_year))


    def test_season_start_year_for_date(self):
        date_exp_season_start_year = [
            (pymmwr.mmwr_week_to_date(2016, 29), 2015),
            (pymmwr.mmwr_week_to_date(2016, 30), 2016),
            (pymmwr.mmwr_week_to_date(2016, 52), 2016),
            (pymmwr.mmwr_week_to_date(2017, 1), 2016),
            (pymmwr.mmwr_week_to_date(2017, 29), 2016),
            (pymmwr.mmwr_week_to_date(2017, 30), 2017),
        ]
        for date, exp_season_start_year in date_exp_season_start_year:
            self.assertEqual(exp_season_start_year, season_start_year_for_date(date))


    def test_mean_absolute_error(self):
        # 'mini' season for testing. from:
        #   model_error_calculations.txt -> model_error_calculations.py -> model_error_calculations.xlsx:
        target_to_exp_mae = {'1 wk ahead': 0.215904853,
                             '2 wk ahead': 0.458186984,
                             '3 wk ahead': 0.950515864,
                             '4 wk ahead': 1.482010693}
        for target, exp_mae in target_to_exp_mae.items():
            act_mae = mean_absolute_error(self.forecast_model, 2016, 'US National', target, mock_wili_for_epi_week_fcn)
            self.assertAlmostEqual(exp_mae, act_mae)
