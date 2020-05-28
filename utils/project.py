import csv
import datetime
import io
import json
import logging
from collections import defaultdict
from pathlib import Path

from django.db import connection
from django.db import transaction
from django.utils import timezone

from forecast_app.models import Project, Unit, Target, Forecast, PointPrediction, ForecastModel, BinDistribution, \
    NamedDistribution, SampleDistribution, QuantileDistribution
from forecast_app.models.project import POSTGRES_NULL_VALUE, TRUTH_CSV_HEADER, TimeZero
from utils.utilities import YYYY_MM_DD_DATE_FORMAT


logger = logging.getLogger(__name__)


#
# delete_project_iteratively()
#

@transaction.atomic
def delete_project_iteratively(project):
    """
    An alternative to Project.delete(), deletes the passed Project, but unlike that function, does so by iterating over
    objects that refer to the project before deleting the project itproject. This apparently reduces the memory usage
    enough to allow the below Heroku deletion. See [Deleting projects on Heroku production fails](https://github.com/reichlab/forecast-repository/issues/91).
    """
    logger.info(f"* delete_project_iteratively(): deleting models and forecasts")
    for forecast_model in project.models.iterator():
        logger.info(f"- {forecast_model.pk}")
        for forecast in forecast_model.forecasts.iterator():
            logger.info(f"  = {forecast.pk}")
            forecast.delete()
        forecast_model.delete()

    logger.info(f"delete_project_iteratively(): deleting units")
    for unit in project.units.iterator():
        logger.info(f"- {unit.pk}")
        unit.delete()

    logger.info(f"delete_project_iteratively(): deleting targets")
    for target in project.targets.iterator():
        logger.info(f"- {target.pk}")
        target.delete()

    logger.info(f"delete_project_iteratively(): deleting timezeros")
    for timezero in project.timezeros.iterator():
        logger.info(f"- {timezero.pk}")
        timezero.delete()

    logger.info(f"delete_project_iteratively(): deleting remainder")
    project.delete()  # deletes remaining references: RowCountCache, ScoreCsvFileCache
    logger.info(f"delete_project_iteratively(): done")


#
# config_dict_from_project()
#

def config_dict_from_project(project, request):
    """
    The twin of `create_project_from_json()`, returns a configuration dict for project as passed to that function.

    :param project: a Project
    :param request: required for TargetSerializer's 'id' field
    """
    from forecast_app.serializers import UnitSerializer, TimeZeroSerializer  # avoid circular imports


    unit_serializer_multi = UnitSerializer(project.units, many=True, context={'request': request})
    tz_serializer_multi = TimeZeroSerializer(project.timezeros, many=True, context={'request': request})
    return {'name': project.name, 'is_public': project.is_public, 'description': project.description,
            'home_url': project.home_url, 'logo_url': project.logo_url, 'core_data': project.core_data,
            'time_interval_type': project.time_interval_type_as_str(),
            'visualization_y_label': project.visualization_y_label,
            'units': [dict(_) for _ in unit_serializer_multi.data],  # replace OrderedDicts
            'targets': [_target_dict_for_target(target, request) for target in project.targets.all()],
            'timezeros': [dict(_) for _ in tz_serializer_multi.data]}  # replace OrderedDicts


def _target_dict_for_target(target, request):
    # request is required for TargetSerializer's 'id' field
    from forecast_app.serializers import TargetSerializer  # avoid circular imports


    if target.type is None:
        raise RuntimeError(f"target has no type: {target}")

    serializer = TargetSerializer(target, context={'request': request})
    return serializer.data


#
# create_project_from_json()
#

@transaction.atomic
def create_project_from_json(proj_config_file_path_or_dict, owner, is_validate_only=False):
    """
    Top-level function that creates a Project based on the json configuration file at json_file_path. Errors if one with
    that name already exists. Does not set Project.model_owners, create TimeZeros, load truth data, create Models, or
    load forecasts.

    :param proj_config_file_path_or_dict: either a Path to project config json file OR a dict as loaded from a file.
        See https://docs.zoltardata.com/fileformats/#project-creation-configuration-json for details and
        docs-project.json for an example.
    :param owner: the new Project's owner (a User). used only if not is_validate_only
    :param is_validate_only: controls whether objects are actually created (is_validate_only=False), or whether only
        validation is done but no creation (is_validate_only=True)
    :return: the new Project
    """
    logger.info(f"* create_project_from_json(): started. proj_config_file_path_or_dict="
                f"{proj_config_file_path_or_dict}, owner={owner}, is_validate_only={is_validate_only}")
    if isinstance(proj_config_file_path_or_dict, dict):
        project_dict = proj_config_file_path_or_dict
    elif isinstance(proj_config_file_path_or_dict, Path):
        with open(proj_config_file_path_or_dict) as fp:
            try:
                project_dict = json.load(fp)
            except Exception as ex:
                raise RuntimeError(f"error loading json file. file={proj_config_file_path_or_dict}, ex={ex}")
    else:
        raise RuntimeError(f"proj_config_file_path_or_dict was neither a dict nor a Path. "
                           f"type={type(proj_config_file_path_or_dict).__name__}")  # is blank w/o __name__. unsure why

    # validate project_dict
    actual_keys = set(project_dict.keys())
    expected_keys = {'name', 'is_public', 'description', 'home_url', 'logo_url', 'core_data', 'time_interval_type',
                     'visualization_y_label', 'units', 'targets', 'timezeros'}
    if actual_keys != expected_keys:
        raise RuntimeError(f"Wrong keys in project_dict. difference={expected_keys ^ actual_keys}. "
                           f"expected={expected_keys}, actual={actual_keys}")

    if is_validate_only:
        project = None
        logger.info(f"- no created Project")
    else:
        # error if project already exists
        name = project_dict['name']
        project = Project.objects.filter(name=name).first()  # None if doesn't exist
        if project:
            raise RuntimeError(f"found existing project. name={name}, project={project}")

        project = _create_project(project_dict, owner)
        logger.info(f"- created Project: {project}")

    units = _validate_and_create_units(project, project_dict, is_validate_only)
    logger.info(f"- created {len(units)} Units: {units}")

    targets = _validate_and_create_targets(project, project_dict, is_validate_only)
    logger.info(f"- created {len(targets)} Targets: {targets}")

    timezeros = _validate_and_create_timezeros(project, project_dict, is_validate_only)
    logger.info(f"- created {len(timezeros)} TimeZeros: {timezeros}")

    logger.info(f"* create_project_from_json(): done!")
    return project


def _validate_and_create_units(project, project_dict, is_validate_only=False):
    units = []  # returned instances
    for unit_dict in project_dict['units']:
        if 'name' not in unit_dict:
            raise RuntimeError(f"one of the unit_dicts had no 'name' field. units={project_dict['units']}")

        # valid
        if not is_validate_only:
            # create the Unit, first checking for an existing one
            unit_name = unit_dict['name']
            existing_unit = project.units.filter(name=unit_name).first()
            if existing_unit:
                raise RuntimeError(f"found existing Unit for name={unit_name}")

            units.append(Unit.objects.create(project=project, name=unit_name))
    return units


def _validate_and_create_timezeros(project, project_dict, is_validate_only=False):
    from forecast_app.api_views import validate_and_create_timezero  # avoid circular imports


    timezeros = [validate_and_create_timezero(project, timezero_config, is_validate_only)
                 for timezero_config in project_dict['timezeros']]
    return timezeros if not is_validate_only else []


def _validate_and_create_targets(project, project_dict, is_validate_only=False):
    targets = []
    type_name_to_type_int = {type_name: type_int for type_int, type_name in Target.TARGET_TYPE_CHOICES}
    for target_dict in project_dict['targets']:
        type_name = _validate_target_dict(target_dict, type_name_to_type_int)  # raises RuntimeError if invalid
        if is_validate_only:
            continue

        # valid! create the Target and then supporting 'list' instances: TargetCat, TargetLwr, and TargetRange. atomic
        # so that Targets succeed only if others do too
        with transaction.atomic():
            model_init = {'project': project,
                          'type': type_name_to_type_int[type_name],
                          'name': target_dict['name'],
                          'description': target_dict['description'],
                          'is_step_ahead': target_dict['is_step_ahead']}  # required keys

            # add is_step_ahead
            if target_dict['is_step_ahead']:
                model_init['step_ahead_increment'] = target_dict['step_ahead_increment']

            # add unit
            if 'unit' in target_dict:
                model_init['unit'] = target_dict['unit']

            # instantiate the new Target, first checking for an existing one
            existing_target = project.targets.filter(name=target_dict['name']).first()
            if existing_target:
                raise RuntimeError(f"found existing Target for name={target_dict['name']}")

            target = Target.objects.create(**model_init)
            targets.append(target)

            # create two TargetRanges
            if ('range' in target_dict) and target_dict['range']:
                target.set_range(target_dict['range'][0], target_dict['range'][1])

            # create TargetCats and TargetLwrs
            if ('cats' in target_dict) and target_dict['cats']:
                # extra_lwr implements this relationship: "if `range` had been specified as [0, 100] in addition to the
                # above `cats`, then the final bin would be [2.2, 100]."
                extra_lwr = max(target_dict['range']) if ('range' in target_dict) and target_dict['range'] else None
                target.set_cats(target_dict['cats'], extra_lwr)
            if target.type == Target.BINARY_TARGET_TYPE:
                # add the two implicit boolean cats
                target.set_cats([False, True])
    return targets


def _validate_target_dict(target_dict, type_name_to_type_int):
    # check for keys required by all target types. optional keys are tested below
    all_keys = set(target_dict.keys())
    tested_keys = all_keys - {'id', 'url', 'unit', 'step_ahead_increment', 'range', 'cats'}  # optional keys
    expected_keys = {'name', 'description', 'type', 'is_step_ahead'}
    if tested_keys != expected_keys:
        raise RuntimeError(f"Wrong required keys in target_dict. difference={expected_keys ^ tested_keys}. "
                           f"expected_keys={expected_keys}, tested_keys={tested_keys}. target_dict={target_dict}")
    # validate type
    type_name = target_dict['type']
    valid_target_types = [type_name for type_int, type_name in Target.TARGET_TYPE_CHOICES]
    if type_name not in valid_target_types:
        raise RuntimeError(f"Invalid type_name={type_name}. valid_target_types={valid_target_types} . "
                           f"target_dict={target_dict}")

    # validate is_step_ahead. field default if not passed is None
    if target_dict['is_step_ahead'] is None:
        raise RuntimeError(f"is_step_ahead not found but is required")

    # check for step_ahead_increment required if is_step_ahead
    if target_dict['is_step_ahead'] and ('step_ahead_increment' not in target_dict):
        raise RuntimeError(f"step_ahead_increment not found but is required when is_step_ahead is passed. "
                           f"target_dict={target_dict}")

    # check required, optional, and invalid keys by target type. 3 cases: 'unit', 'range', 'cats'
    type_int = type_name_to_type_int[type_name]

    # 1) test optional 'unit'. three cases a-c follow

    # 1a) required but not passed: ['continuous', 'discrete', 'date']
    if ('unit' not in all_keys) and \
            (type_int in [Target.CONTINUOUS_TARGET_TYPE, Target.DISCRETE_TARGET_TYPE, Target.DATE_TARGET_TYPE]):
        raise RuntimeError(f"'unit' not passed but is required for type_name={type_name}")

    # 1b) optional: ok to pass or not pass: []: no need to validate

    # 1c) invalid but passed: ['nominal', 'binary']
    if ('unit' in all_keys) and \
            (type_int in [Target.NOMINAL_TARGET_TYPE, Target.BINARY_TARGET_TYPE]):
        raise RuntimeError(f"'unit' passed but is invalid for type_name={type_name}")

    # test that unit, if passed to a Target.DATE_TARGET_TYPE, is valid
    if ('unit' in all_keys) and (type_int == Target.DATE_TARGET_TYPE) and \
            (target_dict['unit'] not in Target.DATE_UNITS):
        raise RuntimeError(f"'unit' passed for date target but was not valid. unit={target_dict['unit']!r}, "
                           f"valid_date_units={Target.DATE_UNITS!r}")

    # 2) test optional 'range'. three cases a-c follow

    # 2a) required but not passed: []: no need to validate

    # 2b) optional: ok to pass or not pass: ['continuous', 'discrete']: no need to validate

    # 2c) invalid but passed: ['nominal', 'binary', 'date']
    if ('range' in all_keys) and (
            type_int in [Target.NOMINAL_TARGET_TYPE, Target.BINARY_TARGET_TYPE, Target.DATE_TARGET_TYPE]):
        raise RuntimeError(f"'range' passed but is invalid for type_name={type_name}")

    # 3) test optional 'cats'. three cases a-c follow

    # 3a) required but not passed: ['nominal', 'date']
    if ('cats' not in all_keys) and \
            (type_int in [Target.NOMINAL_TARGET_TYPE, Target.DATE_TARGET_TYPE]):
        raise RuntimeError(f"'cats' not passed but is required for type_name='{type_name}'")

    # 3b) optional: ok to pass or not pass: ['continuous', 'discrete']: no need to validate

    # 3c) invalid but passed: ['binary']
    if ('cats' in all_keys) and (type_int == Target.BINARY_TARGET_TYPE):
        raise RuntimeError(f"'cats' passed but is invalid for type_name={type_name}")

    # validate 'range' if passed. values can be either ints or floats, and must match the target's data type
    data_types = Target.data_types_for_target_type(type_int)  # python types. recall the first is the preferred one
    if 'range' in target_dict:
        for range_str in target_dict['range']:
            try:
                data_types[0](range_str)  # try parsing as an int or float
            except ValueError as ve:
                raise RuntimeError(f"range type did not match data_types. range_str={range_str!r}, "
                                   f"data_types={data_types}, error: {ve}")

        if len(target_dict['range']) != 2:
            raise RuntimeError(f"range did not contain exactly two items: {target_dict['range']}")

    # validate 'cats' if passed. values can strings, ints, or floats, and must match the target's data type. strings
    # can be either dates in YYYY_MM_DD_DATE_FORMAT form or just plain strings.
    if 'cats' in target_dict:
        for cat_str in target_dict['cats']:
            try:
                if type_int == Target.DATE_TARGET_TYPE:
                    datetime.datetime.strptime(cat_str, YYYY_MM_DD_DATE_FORMAT).date()  # try parsing as a date
                else:
                    data_types[0](cat_str)  # try parsing as a string, int, or float
            except ValueError as ve:
                raise RuntimeError(f"could not convert cat to data_type. cat_str={cat_str!r}, "
                                   f"data_type={data_types[0]}, error: {ve}")

    # test range-cat relationships
    if ('cats' in target_dict) and ('range' in target_dict):
        cats = [data_types[0](cat_str) for cat_str in target_dict['cats']]
        the_range = [data_types[0](range_str) for range_str in target_dict['range']]
        if min(cats) != min(the_range):
            raise RuntimeError(f"the minimum cat ({min(cats)}) did not equal the range's lower bound "
                               f"({min(the_range)})")

        if max(cats) >= max(the_range):
            raise RuntimeError(f"the maximum cat ({max(cats)}) was not less than the range's upper bound "
                               f"({max(the_range)})")

    return type_name


def _create_project(project_dict, owner):
    # validate time_interval_type - one of: 'week', 'biweek', or 'month'
    time_interval_type_input = project_dict['time_interval_type'].lower()
    time_interval_type = None
    for db_value, human_readable_value in Project.TIME_INTERVAL_TYPE_CHOICES:
        if human_readable_value.lower() == time_interval_type_input:
            time_interval_type = db_value

    if time_interval_type is None:
        time_interval_type_choices = [choice[1] for choice in Project.TIME_INTERVAL_TYPE_CHOICES]
        raise RuntimeError(f"invalid 'time_interval_type': {time_interval_type_input}. must be one of: "
                           f"{time_interval_type_choices}")

    project = Project.objects.create(
        owner=owner,
        is_public=project_dict['is_public'],
        name=project_dict['name'],
        time_interval_type=time_interval_type,
        visualization_y_label=(project_dict['visualization_y_label']),
        description=project_dict['description'],
        home_url=project_dict['home_url'],  # required
        logo_url=project_dict['logo_url'] if 'logo_url' in project_dict else None,
        core_data=project_dict['core_data'] if 'core_data' in project_dict else None,
    )
    project.save()
    return project


#
# load_truth_data()
#

@transaction.atomic
def load_truth_data(project, truth_file_path_or_fp, file_name=None, is_convert_na_none=False):
    """
    Loads the data in truth_file_path (see below for file format docs). Like load_csv_data(), uses direct SQL for
    performance, using a fast Postgres-specific routine if connected to it. Note that this method should be called
    after all TimeZeros are created b/c truth data is validated against them. Notes:

    - TimeZeros "" b/c truth timezeros are validated against project ones
    - One csv file/project, which includes timezeros across all seasons.
    - Columns: timezero, unit, target, value . NB: There is no season information (see below). timezeros are
      formatted “yyyymmdd”. A header must be included.
    - Missing timezeros: If the program generating the csv file does not have information for a particular project
      timezero, then it should not generate a value for it. (The alternative would be to require the program to
      generate placeholder values for missing dates.)
    - Non-numeric values: Some targets will have no value, such as season onset when a baseline is not met. In those
      cases, the value should be “NA”, per
      https://predict.cdc.gov/api/v1/attachments/flusight/flu_challenge_2016-17_update.docx.
    - For date-based onset or peak targets, values must be dates in the same format as timezeros, rather than
        project-specific time intervals such as an epidemic week.
    - Validation:
        - Every timezero in the csv file must have a matching one in the project. Note that the inverse is not
          necessarily true, such as in the case above of missing timezeros.
        - Every unit in the csv file must a matching one in the Project.
        - Ditto for every target.

    :param truth_file_path_or_fp: Path to csv file with the truth data, one line per timezero|unit|target
        combination, OR an already-open file-like object
    :param file_name: name to use for the file
    :param is_convert_na_none: as passed to Target.is_value_compatible_with_target_type()
    """
    logger.debug(f"load_truth_data(): entered. truth_file_path_or_fp={truth_file_path_or_fp}, "
                 f"file_name={file_name}")
    if not project.pk:
        raise RuntimeError("instance is not saved the the database, so can't insert data: {!r}".format(project))

    logger.debug(f"load_truth_data(): calling delete_truth_data()")
    project.delete_truth_data()

    logger.debug(f"load_truth_data(): calling _load_truth_data()")
    # https://stackoverflow.com/questions/1661262/check-if-object-is-file-like-in-python
    if isinstance(truth_file_path_or_fp, io.IOBase):
        num_rows = _load_truth_data(project, truth_file_path_or_fp, is_convert_na_none)
    else:
        with open(str(truth_file_path_or_fp)) as cdc_csv_file_fp:
            num_rows = _load_truth_data(project, cdc_csv_file_fp, is_convert_na_none)

    # done
    logger.debug(f"load_truth_data(): saving. num_rows: {num_rows}")
    project.truth_csv_filename = file_name or truth_file_path_or_fp.name
    project.truth_updated_at = timezone.now()
    project.save()
    project._update_model_score_changes()
    logger.debug(f"load_truth_data(): done")


def _load_truth_data(project, cdc_csv_file_fp, is_convert_na_none):
    from forecast_app.models import TruthData  # avoid circular imports


    with connection.cursor() as cursor:
        # validates, and replaces value to the five typed values:
        rows = _load_truth_data_rows(project, cdc_csv_file_fp, is_convert_na_none)
        if not rows:
            return 0

        truth_data_table_name = TruthData._meta.db_table
        columns = [TruthData._meta.get_field('time_zero').column,
                   TruthData._meta.get_field('unit').column,
                   TruthData._meta.get_field('target').column,
                   'value_i', 'value_f', 'value_t', 'value_d', 'value_b']  # only one of value_* is non-None
        if connection.vendor == 'postgresql':
            string_io = io.StringIO()
            csv_writer = csv.writer(string_io, delimiter=',')
            for timezero_id, unit_id, target_id, value_i, value_f, value_t, value_d, value_b in rows:
                # note that we translate None -> POSTGRES_NULL_VALUE for the nullable column
                csv_writer.writerow([timezero_id, unit_id, target_id,
                                     value_i if value_i is not None else POSTGRES_NULL_VALUE,
                                     value_f if value_f is not None else POSTGRES_NULL_VALUE,
                                     value_t if value_t is not None else POSTGRES_NULL_VALUE,
                                     value_d if value_d is not None else POSTGRES_NULL_VALUE,
                                     value_b if value_b is not None else POSTGRES_NULL_VALUE])
            string_io.seek(0)
            cursor.copy_from(string_io, truth_data_table_name, columns=columns, sep=',', null=POSTGRES_NULL_VALUE)
        else:  # 'sqlite', etc.
            sql = """
                INSERT INTO {truth_data_table_name} ({column_names})
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s);
            """.format(truth_data_table_name=truth_data_table_name, column_names=(', '.join(columns)))
            cursor.executemany(sql, rows)
    return len(rows)


def _load_truth_data_rows(project, csv_file_fp, is_convert_na_none):
    """
    Similar to _cleaned_rows_from_cdc_csv_file(), loads, validates, and cleans the rows in csv_file_fp. Replaces value
    with the five typed values.
    """
    csv_reader = csv.reader(csv_file_fp, delimiter=',')

    # validate header
    try:
        orig_header = next(csv_reader)
    except StopIteration:
        raise RuntimeError("empty file")

    header = orig_header
    header = [h.lower() for h in [i.replace('"', '') for i in header]]
    if header != TRUTH_CSV_HEADER:
        raise RuntimeError(f"invalid header. orig_header={orig_header!r}, "
                           f"expected header={TRUTH_CSV_HEADER!r}")

    # collect the rows. first we load them all into memory (processing and validating them as we go)
    unit_names_to_pks = {unit.name: unit.id for unit in project.units.all()}
    target_name_to_object = {target.name: target for target in project.targets.all()}
    rows = []
    timezero_to_missing_count = defaultdict(int)  # to minimize warnings
    unit_to_missing_count = defaultdict(int)
    target_to_missing_count = defaultdict(int)
    for row in csv_reader:
        if len(row) != 4:
            raise RuntimeError("Invalid row (wasn't 4 columns): {!r}".format(row))

        timezero_date, unit_name, target_name, value = row

        # validate timezero_date
        # todo cache: time_zero_for_timezero_date() results - expensive?
        time_zero = project.time_zero_for_timezero_date(
            datetime.datetime.strptime(timezero_date, YYYY_MM_DD_DATE_FORMAT))
        if not time_zero:
            timezero_to_missing_count[timezero_date] += 1
            continue

        # validate unit and target
        if unit_name not in unit_names_to_pks:
            unit_to_missing_count[unit_name] += 1
            continue

        if target_name not in target_name_to_object:
            target_to_missing_count[target_name] += 1
            continue

        # replace value with the five typed values - similar to _replace_value_with_five_types(). note that at this
        # point value is a str, so we ask Target.is_value_compatible_with_target_type needs to try converting to the
        # correct data type
        target = target_name_to_object[target_name]
        data_types = target.data_types()  # python types. recall the first is the preferred one
        is_compatible, parsed_value = Target.is_value_compatible_with_target_type(target.type, value, is_coerce=True,
                                                                                  is_convert_na_none=is_convert_na_none)
        if not is_compatible:
            raise RuntimeError(f"value was not compatible with target data type. value={value!r}, "
                               f"data_types={data_types}")

        # validate: For `discrete` and `continuous` targets (if `range` is specified):
        # - The entry in the `value` column for a specific `target`-`unit`-`timezero` combination must be contained
        #   within the `range` of valid values for the target. If `cats` is specified but `range` is not, then there is
        #   an implicit range for the ground truth value, and that is between min(`cats`) and \infty.
        # recall: "The range is assumed to be inclusive on the lower bound and open on the upper bound, # e.g. [a, b)."
        cats_values = target.cats_values()  # datetime.date instances for date targets
        range_tuple = target.range_tuple() or (min(cats_values), float('inf')) if cats_values else None
        if (target.type in [Target.DISCRETE_TARGET_TYPE, Target.CONTINUOUS_TARGET_TYPE]) and range_tuple \
                and (parsed_value is not None) and not (range_tuple[0] <= parsed_value < range_tuple[1]):
            raise RuntimeError(f"The entry in the `value` column for a specific `target`-`unit`-`timezero` "
                               f"combination must be contained within the range of valid values for the target. "
                               f"value={parsed_value!r}, range_tuple={range_tuple}")

        # validate: For `nominal` and `date` target_types:
        #  - The entry in the `cat` column for a specific `target`-`unit`-`timezero` combination must be contained
        #    within the set of valid values for the target, as defined by the project config file.
        cats_values = set(target.cats_values())  # datetime.date instances for date targets
        if (target.type in [Target.NOMINAL_TARGET_TYPE, Target.DATE_TARGET_TYPE]) and cats_values \
                and (parsed_value not in cats_values):
            raise RuntimeError(f"The entry in the `cat` column for a specific `target`-`unit`-`timezero` "
                               f"combination must be contained within the set of valid values for the target. "
                               f"parsed_value={parsed_value}, cats_values={cats_values}")

        # valid
        value_i = parsed_value if data_types[0] == Target.INTEGER_DATA_TYPE else None
        value_f = parsed_value if data_types[0] == Target.FLOAT_DATA_TYPE else None
        value_t = parsed_value if data_types[0] == Target.TEXT_DATA_TYPE else None
        value_d = parsed_value if data_types[0] == Target.DATE_DATA_TYPE else None
        value_b = parsed_value if data_types[0] == Target.BOOLEAN_DATA_TYPE else None

        rows.append((time_zero.pk, unit_names_to_pks[unit_name], target.pk,
                     value_i, value_f, value_t, value_d, value_b))

    # report warnings
    for time_zero, count in timezero_to_missing_count.items():
        logger.warning("_load_truth_data_rows(): timezero not found in project: {}: {} row(s)"
                       .format(time_zero, count))
    for unit_name, count in unit_to_missing_count.items():
        logger.warning("_load_truth_data_rows(): Unit not found in project: {!r}: {} row(s)"
                       .format(unit_name, count))
    for target_name, count in target_to_missing_count.items():
        logger.warning("_load_truth_data_rows(): Target not found in project: {!r}: {} row(s)"
                       .format(target_name, count))

    # done
    return rows


#
# query_forecasts()
#

CSV_HEADER = ['model', 'timezero', 'season', 'unit', 'target', 'class', 'value', 'cat', 'prob', 'sample', 'quantile',
              'family', 'param1', 'param2', 'param3']


def query_forecasts_for_project(project, query, max_num_rows=100_000):
    """
    Top-level function for querying forecasts within project. Runs in the calling thread and therefore blocks.

    Returns a list of rows in a Zoltar-specific CSV row format. The columns are defined in CSV_HEADER. Note that the
    csv is 'sparse': not every row uses all columns, and unused ones are empty (''). However, the first four columns
    are always non-empty, i.e., every prediction has them.

    The 'class' of each row is named to be the same as Zoltar's utils.forecast.PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS
    variable. Column ordering is CSV_HEADER.

    `query` is documented at https://docs.zoltardata.com/, but briefly it is a dict that contains up to five keys. The
    first four are object IDs corresponding to each one's class, and the last is a list of strings:

    - 'models': optional list of ForecastModel IDs
    - 'units': "" Unit IDs
    - 'targets': "" Target IDs
    - 'timezeros': "" TimeZero IDs
    - 'types': optional list of str types as defined in PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS keys

    :param project: a Project
    :param query: a dict specifying the query parameters. see https://docs.zoltardata.com/ for documentation, and above
        for a summary
    :param max_num_rows: the number of rows at which this function raises a RuntimeError
    :return: a list of CSV rows including CSV_HEADER
    """
    from utils.forecast import PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS  # avoid circular imports


    # validate query and set query defaults ("all in project") if necessary
    logger.debug(f"query_forecasts_for_project(): validating query: {query}. project={project}")
    error_messages, (model_ids, unit_ids, target_ids, timezero_ids, types) = validate_forecasts_query(project, query)
    if not model_ids:
        model_ids = project.models.all().values_list('id', flat=True)  # default to all ForecastModels in Project
    if not unit_ids:
        unit_ids = project.units.all().values_list('id', flat=True)  # "" Units ""
    if not target_ids:
        target_ids = project.targets.all().values_list('id', flat=True)  # "" Targets ""
    if not timezero_ids:
        timezero_ids = project.timezeros.all().values_list('id', flat=True)  # "" TimeZeros ""

    # get which types to include
    is_include_bin = (not types) or (PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS[BinDistribution] in types)
    is_include_named = (not types) or (PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS[NamedDistribution] in types)
    is_include_point = (not types) or (PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS[PointPrediction] in types)
    is_include_sample = (not types) or (PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS[SampleDistribution] in types)
    is_include_quantile = (not types) or (PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS[QuantileDistribution] in types)

    # get Forecasts to be included, applying query's constraints
    forecast_ids = Forecast.objects.filter(forecast_model__id__in=model_ids,
                                           time_zero__id__in=timezero_ids) \
        .values_list('id', flat=True)

    # create queries for each prediction type, but don't execute them yet. first check # rows and limit if necessary.
    # note that not all will be executed, depending on the 'types' key
    bin_qs = BinDistribution.objects.filter(forecast__id__in=forecast_ids,
                                            unit__id__in=unit_ids,
                                            target__id__in=target_ids) \
        .values_list('forecast__forecast_model__id', 'forecast__time_zero__id', 'unit__name', 'target__name',
                     'prob', 'cat_i', 'cat_f', 'cat_t', 'cat_d', 'cat_b')
    named_qs = NamedDistribution.objects.filter(forecast__id__in=forecast_ids,
                                                unit__id__in=unit_ids,
                                                target__id__in=target_ids) \
        .values_list('forecast__forecast_model__id', 'forecast__time_zero__id', 'unit__name', 'target__name',
                     'family', 'param1', 'param2', 'param3')
    point_qs = PointPrediction.objects.filter(forecast__id__in=forecast_ids,
                                              unit__id__in=unit_ids,
                                              target__id__in=target_ids) \
        .values_list('forecast__forecast_model__id', 'forecast__time_zero__id', 'unit__name', 'target__name',
                     'value_i', 'value_f', 'value_t', 'value_d', 'value_b')
    sample_qs = SampleDistribution.objects.filter(forecast__id__in=forecast_ids,
                                                  unit__id__in=unit_ids,
                                                  target__id__in=target_ids) \
        .values_list('forecast__forecast_model__id', 'forecast__time_zero__id', 'unit__name', 'target__name',
                     'sample_i', 'sample_f', 'sample_t', 'sample_d', 'sample_b')
    quantile_qs = QuantileDistribution.objects.filter(forecast__id__in=forecast_ids,
                                                      unit__id__in=unit_ids,
                                                      target__id__in=target_ids) \
        .values_list('forecast__forecast_model__id', 'forecast__time_zero__id', 'unit__name', 'target__name',
                     'quantile', 'value_i', 'value_f', 'value_d')

    # count number of rows to query, and error if too many
    is_include_query_set_pred_types = [(is_include_bin, bin_qs, 'bin'),
                                       (is_include_named, named_qs, 'named'),
                                       (is_include_point, point_qs, 'point'),
                                       (is_include_sample, sample_qs, 'sample'),
                                       (is_include_quantile, quantile_qs, 'quantile')]
    pred_type_counts = [(pred_type, query_set.count()) for is_include, query_set, pred_type
                        in is_include_query_set_pred_types if is_include]
    num_rows = sum([_[1] for _ in pred_type_counts])
    logger.debug(f"query_forecasts_for_project(): preparing to query. pred_type_counts={pred_type_counts}. total "
                 f"num_rows={num_rows}")
    if num_rows > max_num_rows:
        raise RuntimeError(f"number of rows exceeded maximum. num_rows={num_rows}, max_num_rows={max_num_rows}")

    # add rows for each Prediction subclass
    rows = [CSV_HEADER]  # return value. filled next
    forecast_model_id_to_obj = {forecast_model.pk: forecast_model for forecast_model in project.models.all()}
    timezero_id_to_obj = {timezero.pk: timezero for timezero in project.timezeros.all()}
    timezero_to_season_name = project.timezero_to_season_name()

    # add BinDistributions
    if is_include_bin:
        logger.debug(f"query_forecasts_for_project(): getting BinDistributions")
        # class-specific columns all default to empty:
        value, cat, prob, sample, quantile, family, param1, param2, param3 = '', '', '', '', '', '', '', '', ''
        for forecast_model_id, timezero_id, unit_name, target_name, prob, cat_i, cat_f, cat_t, cat_d, cat_b in bin_qs:
            model_str, timezero_str, season, class_str = _model_tz_season_class_strs(
                forecast_model_id_to_obj[forecast_model_id], timezero_id_to_obj[timezero_id], timezero_to_season_name,
                BinDistribution)
            cat = PointPrediction.first_non_none_value(cat_i, cat_f, cat_t, cat_d, cat_b)
            cat = cat.strftime(YYYY_MM_DD_DATE_FORMAT) if isinstance(cat, datetime.date) else cat
            rows.append([model_str, timezero_str, season, unit_name, target_name, class_str,
                         value, cat, prob, sample, quantile, family, param1, param2, param3])

    # add NamedDistributions
    if is_include_named:
        logger.debug(f"query_forecasts_for_project(): getting NamedDistributions")
        # class-specific columns all default to empty:
        value, cat, prob, sample, quantile, family, param1, param2, param3 = '', '', '', '', '', '', '', '', ''
        for forecast_model_id, timezero_id, unit_name, target_name, family, param1, param2, param3 in named_qs:
            model_str, timezero_str, season, class_str = _model_tz_season_class_strs(
                forecast_model_id_to_obj[forecast_model_id], timezero_id_to_obj[timezero_id], timezero_to_season_name,
                NamedDistribution)
            family = NamedDistribution.FAMILY_CHOICE_TO_ABBREVIATION[family]
            rows.append([model_str, timezero_str, season, unit_name, target_name, class_str,
                         value, cat, prob, sample, quantile, family, param1, param2, param3])

    # add PointPredictions
    if is_include_point:
        logger.debug(f"query_forecasts_for_project(): getting PointPredictions")
        # class-specific columns all default to empty:
        value, cat, prob, sample, quantile, family, param1, param2, param3 = '', '', '', '', '', '', '', '', ''
        for forecast_model_id, timezero_id, unit_name, target_name, value_i, value_f, value_t, value_d, value_b \
                in point_qs:
            model_str, timezero_str, season, class_str = _model_tz_season_class_strs(
                forecast_model_id_to_obj[forecast_model_id], timezero_id_to_obj[timezero_id], timezero_to_season_name,
                PointPrediction)
            value = PointPrediction.first_non_none_value(value_i, value_f, value_t, value_d, value_b)
            value = value.strftime(YYYY_MM_DD_DATE_FORMAT) if isinstance(value, datetime.date) else value
            rows.append([model_str, timezero_str, season, unit_name, target_name, class_str,
                         value, cat, prob, sample, quantile, family, param1, param2, param3])

    # add SampleDistribution
    if is_include_sample:
        logger.debug(f"query_forecasts_for_project(): getting SampleDistributions")
        # class-specific columns all default to empty:
        value, cat, prob, sample, quantile, family, param1, param2, param3 = '', '', '', '', '', '', '', '', ''
        for forecast_model_id, timezero_id, unit_name, target_name, \
            sample_i, sample_f, sample_t, sample_d, sample_b in sample_qs:
            model_str, timezero_str, season, class_str = _model_tz_season_class_strs(
                forecast_model_id_to_obj[forecast_model_id], timezero_id_to_obj[timezero_id], timezero_to_season_name,
                SampleDistribution)
            sample = PointPrediction.first_non_none_value(sample_i, sample_f, sample_t, sample_d, sample_b)
            sample = sample.strftime(YYYY_MM_DD_DATE_FORMAT) if isinstance(sample, datetime.date) else sample
            rows.append([model_str, timezero_str, season, unit_name, target_name, class_str,
                         value, cat, prob, sample, quantile, family, param1, param2, param3])

    # add QuantileDistribution
    if is_include_quantile:
        logger.debug(f"query_forecasts_for_project(): getting QuantileDistributions")
        # class-specific columns all default to empty:
        value, cat, prob, sample, quantile, family, param1, param2, param3 = '', '', '', '', '', '', '', '', ''
        for forecast_model_id, timezero_id, unit_name, target_name, quantile, value_i, value_f, value_d in quantile_qs:
            model_str, timezero_str, season, class_str = _model_tz_season_class_strs(
                forecast_model_id_to_obj[forecast_model_id], timezero_id_to_obj[timezero_id], timezero_to_season_name,
                QuantileDistribution)
            value = PointPrediction.first_non_none_value(value_i, value_f, None, value_d, None)
            value = value.strftime(YYYY_MM_DD_DATE_FORMAT) if isinstance(value, datetime.date) else value
            rows.append([model_str, timezero_str, season, unit_name, target_name, class_str,
                         value, cat, prob, sample, quantile, family, param1, param2, param3])

    # NB: we do not sort b/c it's expensive
    logger.debug(f"query_forecasts_for_project(): done: {len(rows)} rows")
    return rows


def _model_tz_season_class_strs(forecast_model, time_zero, timezero_to_season_name, prediction_class):
    from utils.forecast import PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS  # avoid circular imports


    model_str = forecast_model.abbreviation if forecast_model.abbreviation else forecast_model.name
    timezero_str = time_zero.timezero_date.strftime(YYYY_MM_DD_DATE_FORMAT)
    season = timezero_to_season_name[time_zero]
    class_str = PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS[prediction_class]
    return model_str, timezero_str, season, class_str


def validate_forecasts_query(project, query):
    """
    Validates `query` according to the parameters documented at https://docs.zoltardata.com/ .

    :param project: as passed from `query_forecasts_for_project()`
    :param query: ""
    :return: a 2-tuple: (error_messages, (model_ids, unit_ids, target_ids, timezero_ids, types)) . notice the second
        element is itself a 5-tuple of validated object IDs
    """
    from utils.forecast import PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS  # avoid circular imports


    # return value. filled next
    error_messages, model_ids, unit_ids, target_ids, timezero_ids, types = [], [], [], [], [], []

    # validate query type
    if not isinstance(query, dict):
        error_messages.append(f"query was not a dict: {query}, query type={type(query)}")
        return [error_messages, (model_ids, unit_ids, target_ids, timezero_ids, types)]

    # validate keys
    actual_keys = set(query.keys())
    expected_keys = {'models', 'units', 'targets', 'timezeros', 'types'}
    if not (actual_keys <= expected_keys):
        error_messages.append(f"one or more query keys was invalid. query={query}, actual_keys={actual_keys}, "
                              f"expected_keys={expected_keys}")
        # return even though we could technically continue
        return [error_messages, (model_ids, unit_ids, target_ids, timezero_ids, types)]

    # validate object IDs
    if 'models' in query:
        model_ids = query['models']
        error_messages.extend(_validate_object_ids('models', model_ids, project, ForecastModel))
    if 'units' in query:
        unit_ids = query['units']
        error_messages.extend(_validate_object_ids('units', unit_ids, project, Unit))
    if 'timezeros' in query:
        timezero_ids = query['timezeros']
        error_messages.extend(_validate_object_ids('timezeros', timezero_ids, project, TimeZero))
    if 'targets' in query:
        target_ids = query['targets']
        error_messages.extend(_validate_object_ids('targets', target_ids, project, Target))

    # validate Prediction types
    if 'types' in query:
        types = query['types']
        if not (set(types) <= set(PREDICTION_CLASS_TO_JSON_IO_DICT_CLASS.values())):
            error_messages.append(f"one or more types were invalid prediction types. types={types}, query={query}")
            return [error_messages, (model_ids, unit_ids, target_ids, timezero_ids, types)]

    # valid!
    return [error_messages, (model_ids, unit_ids, target_ids, timezero_ids, types)]


def _validate_object_ids(query_key, object_ids, project, model_class):
    """
    Helper function that validates a list of object ideas of type `model_class`. Returns error_messages.
    """
    error_messages = []  # return value. filled next
    if not all(map(lambda _: isinstance(_, int), object_ids)):
        error_messages.append(f"`{query_key}` contained non-int value(s): {object_ids!r}")
    else:
        is_exist_ids = [model_class.objects.filter(project_id=project.pk, pk=model_id).exists()
                        for model_id in object_ids]
        if not all(is_exist_ids):
            missing_ids = [model_id for is_exist_id, model_id in zip(is_exist_ids, object_ids) if is_exist_id]
            error_messages.append(f"`{query_key}` contained ID(s) of objects that don't exist in project: "
                                  f"{missing_ids}")
    return error_messages
