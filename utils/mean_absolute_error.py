import logging
from itertools import groupby

from forecast_app.models import ForecastData, Forecast
from forecast_app.models.data import CDCData


logger = logging.getLogger(__name__)


def location_to_mean_abs_error_rows_for_project(project, season_name):
    """
    Called by the project_scores() view function, returns a dict containing a table of mean absolute errors for
    all models and all locations in project for season_name. The dict maps:
    {location: (mean_abs_error_rows, target_to_min_mae)}, where rows is a table in the form of a list of rows where each
    row corresponds to a model, and each column corresponds to a target, i.e., X=target vs. Y=Model.

    See _mean_abs_error_rows_for_project() for the format of mean_abs_error_rows.

    Returns {} if no truth data or no appropriate target_names in project.
    """
    if not project.is_truth_data_loaded():  # no reason to do all the work
        return {}

    target_names = [target.name for target in project.visualization_targets()]
    if not target_names:
        return {}

    # cache all the data we need for all models
    logger.debug("location_to_mean_abs_error_rows_for_project(): calling: _model_id_to_point_values_dict(). "
                 "project={}, season_name={}, target_names={}".format(project, season_name, target_names))
    model_id_to_point_values_dict = _model_id_to_point_values_dict(project, season_name, target_names)
    logger.debug("location_to_mean_abs_error_rows_for_project(): calling: _model_id_to_forecast_id_tz_dates()")
    model_id_to_forecast_id_tz_dates = _model_id_to_forecast_id_tz_dates(project, season_name)
    logger.debug("location_to_mean_abs_error_rows_for_project(): calling: _mean_abs_error_rows_for_project(), multiple")
    loc_target_tz_date_to_truth = project.location_target_name_tz_date_to_truth(season_name)  # target__id
    forecast_models = project.models.order_by('name')

    for forecast_model in forecast_models:
        if not forecast_model.forecasts.exists():
            raise RuntimeError("Could not calculate absolute errors: model had no data: {}".format(forecast_model))

    location_to_mean_abs_error_rows = {
        location: _mean_abs_error_rows_for_project(forecast_models, target_names, location,
                                                   model_id_to_point_values_dict, model_id_to_forecast_id_tz_dates,
                                                   loc_target_tz_date_to_truth)
        for location in project.get_locations()}
    logger.debug("location_to_mean_abs_error_rows_for_project(): done")
    return location_to_mean_abs_error_rows


def _mean_abs_error_rows_for_project(forecast_models, target_names, location, model_id_to_point_values_dict,
                                     model_id_to_forecast_id_tz_dates, loc_target_tz_date_to_truth):
    """
    Returns a 2-list of the form: (rows, target_to_min_mae), where rows is a table in the form of a list of rows where
    each row corresponds to a model, and each column corresponds to a target, i.e., X=target vs. Y=Model. The format:

        [[model1_pk, target1_mae, target2_mae, ...], ...]

    The first row is the header.

    Recall the Mean Absolute Error table from http://reichlab.io/flusight/ , such as for these settings:

        US National > 2016-2017 > 1 wk, 2 wk, 3 wk, 4 wk ->

        +----------+------+------+------+------+
        | Model    | 1 wk | 2 wk | 3 wk | 4 wk |
        +----------+------+------+------+------+
        | kcde     | 0.29 | 0.45 | 0.61 | 0.69 |
        | kde      | 0.58 | 0.59 | 0.6  | 0.6  |
        | sarima   | 0.23 | 0.35 | 0.49 | 0.56 |
        | ensemble | 0.3  | 0.4  | 0.53 | 0.54 |
        +----------+------+------+------+------+

    The second return arg - target_to_min_mae - is a dict that maps: {target_ minimum_mae). Returns ([], {}) if the
    project does not have appropriate target_names defined in its configuration. NB: assumes all of project's models have the
    same target_names - something is validated by ForecastModel.load_forecast()
    """
    logger.debug("_mean_abs_error_rows_for_project(): entered. forecast_models={}, target_names={}, location={}"
                 .format(forecast_models, target_names, location))
    target_to_min_mae = {target: None for target in target_names}  # tracks min MAE for bolding in table. filled next
    rows = [['Model', *target_names]]  # header
    for forecast_model in forecast_models:
        row = [forecast_model.pk]
        for target_name in target_names:
            forecast_to_point_dict = model_id_to_point_values_dict[forecast_model.pk] \
                if forecast_model.pk in model_id_to_point_values_dict \
                else {}
            forecast_id_tz_dates = model_id_to_forecast_id_tz_dates[forecast_model.pk] \
                if forecast_model.pk in model_id_to_forecast_id_tz_dates \
                else {}
            mae_val = mean_absolute_error(forecast_model, location, target_name,
                                          forecast_to_point_dict, forecast_id_tz_dates, loc_target_tz_date_to_truth)
            if not mae_val:
                return [rows, {}]  # just header

            target_to_min_mae[target_name] = min(mae_val, target_to_min_mae[target_name]) \
                if target_to_min_mae[target_name] else mae_val
            row.append(mae_val)
        rows.append(row)

    logger.debug("_mean_abs_error_rows_for_project(): done")
    return [rows, target_to_min_mae]


def mean_absolute_error(forecast_model, location, target_name,
                        forecast_to_point_dict, forecast_id_tz_dates, loc_target_tz_date_to_truth):
    """
    Calculates the mean absolute error for the passed model and parameters. Note: Uses cached values
    (forecast_to_point_dict and forecast_id_tz_dates) instead of hitting the database directly, for
    speed.

    :param: forecast_model: ForecastModel whose forecasts are used for the calculation
    :param: location: a location in the model
    :param: target_name: "" target_name ""
    :param: forecast_to_point_dict: cached points for forecast_model as returned by _model_id_to_point_values_dict()
    :param: forecast_id_tz_dates: cached rows for forecast_model as returned by _model_id_to_forecast_id_tz_dates()
    :return: mean absolute error (scalar) for my predictions for a location and target_name. returns None if can't be
        calculated
    """
    forecast_id_to_abs_error = {}
    for forecast_id, forecast_timezero_date in forecast_id_tz_dates:
        try:
            truth_values = loc_target_tz_date_to_truth[location][target_name][forecast_timezero_date]
        except KeyError as ke:
            logger.warning("mean_absolute_error(): loc_target_tz_date_to_truth was missing a key: {}. location={}, "
                           "target_name={}, forecast_timezero_date={}. loc_target_tz_date_to_truth={}"
                           .format(ke.args, location, target_name, forecast_timezero_date, loc_target_tz_date_to_truth))
            continue  # skip this forecast's contribution to the score

        if len(truth_values) == 0:  # truth not available
            logger.warning("mean_absolute_error(): truth value not found. forecast_model={}, location={!r}, "
                           "target_name={!r}, forecast_id={}, forecast_timezero_date={}"
                           .format(forecast_model, location, target_name, forecast_id, forecast_timezero_date))
            continue  # skip this forecast's contribution to the score
        elif len(truth_values) > 1:
            logger.warning("mean_absolute_error(): >1 truth values found. forecast_model={}, location={!r}, "
                           "target_name={!r}, forecast_id={}, forecast_timezero_date={}, truth_values={}"
                           .format(forecast_model, location, target_name, forecast_id, forecast_timezero_date,
                                   truth_values))
            continue  # skip this forecast's contribution to the score

        true_value = truth_values[0]
        if true_value is None:
            logger.warning(
                "mean_absolute_error(): truth value was NA. forecast_id={}, location={!r}, target_name={!r}, "
                "forecast_timezero_date={}".format(forecast_id, location, target_name, forecast_timezero_date))
            continue  # skip this forecast's contribution to the score

        predicted_value = forecast_to_point_dict[forecast_id][location][target_name]
        abs_error = abs(predicted_value - true_value)
        forecast_id_to_abs_error[forecast_id] = abs_error

    return (sum(forecast_id_to_abs_error.values()) / len(forecast_id_to_abs_error)) if forecast_id_to_abs_error \
        else None


def _model_id_to_forecast_id_tz_dates(project, season_name):
    """
    Returns a dict for forecast_models and season_name that maps: ForecastModel.pk -> 2-tuple of the form:
    (forecast_id, forecast_timezero_date). This is an optimization that avoids some ORM overhead when simply iterating
    like so: `for forecast in forecast_model.forecasts.all(): ...`
    """
    # get the rows, ordered so we can groupby()
    season_start_date, season_end_date = project.start_end_dates_for_season(season_name)
    rows = Forecast.objects \
        .filter(forecast_model__project=project,
                time_zero__timezero_date__gte=season_start_date,
                time_zero__timezero_date__lte=season_end_date) \
        .order_by('forecast_model__id') \
        .values_list('forecast_model__id', 'id', 'time_zero__timezero_date')

    # build the dict
    logger.debug("_model_id_to_forecast_id_tz_dates(): building model_id_to_forecast_id_tz_date")
    model_id_to_forecast_id_tz_date = {}  # return value. filled next
    for model_pk, forecast_row_grouper in groupby(rows, key=lambda _: _[0]):
        model_id_to_forecast_id_tz_date[model_pk] = [row[1:] for row in forecast_row_grouper]

    logger.debug("_model_id_to_forecast_id_tz_dates(): done")
    return model_id_to_forecast_id_tz_date


def _model_id_to_point_values_dict(project, season_name, target_names):
    """
    :return: a dict that provides predicted point values for all of project's models, season_name, and target_names.
        The dict drills down as such:

    - model_to_point_dicts: {forecast_model_id -> forecast_to_point_dicts}
    - forecast_to_point_dicts: {forecast_id -> location_to_point_dicts}
    - location_to_point_dicts: {location -> target_to_points_dicts}
    - target_to_points_dicts: {target -> point_value}
    """
    # get the rows, ordered so we can groupby()
    season_start_date, season_end_date = project.start_end_dates_for_season(season_name)
    logger.debug("_model_id_to_point_values_dict(): calling: execute()")
    rows = ForecastData.objects \
        .filter(row_type=CDCData.POINT_ROW_TYPE,
                target__name__in=target_names,
                forecast__forecast_model__project=project,
                forecast__time_zero__timezero_date__gte=season_start_date,
                forecast__time_zero__timezero_date__lte=season_end_date) \
        .order_by('forecast__forecast_model__id', 'forecast__id', 'location') \
        .values_list('forecast__forecast_model__id', 'forecast__id', 'location', 'target__name', 'value')

    # build the dict
    logger.debug("_model_id_to_point_values_dict(): building models_to_point_values_dicts")
    models_to_point_values_dicts = {}  # return value. filled next
    for model_pk, forecast_loc_target_val_grouper in groupby(rows, key=lambda _: _[0]):
        forecast_to_point_dicts = {}
        for forecast_pk, loc_target_val_grouper in groupby(forecast_loc_target_val_grouper, key=lambda _: _[1]):
            location_to_point_dicts = {}
            for location, target_val_grouper in groupby(loc_target_val_grouper, key=lambda _: _[2]):
                grouper_rows = list(target_val_grouper)
                location_to_point_dicts[location] = {grouper_row[3]: grouper_row[4] for grouper_row in grouper_rows}
            forecast_to_point_dicts[forecast_pk] = location_to_point_dicts
        models_to_point_values_dicts[model_pk] = forecast_to_point_dicts

    logger.debug("_model_id_to_point_values_dict(): done")
    return models_to_point_values_dicts
