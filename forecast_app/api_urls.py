from django.conf.urls import url

from forecast_app import api_views


urlpatterns = [
    url(r'^$', api_views.api_root, name='api-root'),

    url(r'^user/(?P<pk>\d+)/$', api_views.UserDetail.as_view(), name='api-user-detail'),

    url(r'^uploadfilejob/(?P<pk>\d+)/$', api_views.UploadFileJobDetailView.as_view(),
        name='api-upload-file-job-detail'),

    url(r'^projects/$', api_views.ProjectList.as_view(), name='api-project-list'),

    url(r'^project/(?P<pk>\d+)/$', api_views.ProjectDetail.as_view(), name='api-project-detail'),
    url(r'^project/(?P<pk>\d+)/units/$', api_views.ProjectUnitList.as_view(), name='api-unit-list'),
    url(r'^project/(?P<pk>\d+)/targets/$', api_views.ProjectTargetList.as_view(), name='api-target-list'),
    url(r'^project/(?P<pk>\d+)/timezeros/$', api_views.ProjectTimeZeroList.as_view(), name='api-timezero-list'),
    url(r'^project/(?P<pk>\d+)/models/$', api_views.ProjectForecastModelList.as_view(), name='api-model-list'),

    url(r'^project/(?P<pk>\d+)/truth/$', api_views.TruthDetail.as_view(), name='api-truth-detail'),
    url(r'^project/(?P<pk>\d+)/truth_data_download/$', api_views.download_truth_data, name='api-download-truth-data'),

    url(r'^project/(?P<pk>\d+)/score_data/$', api_views.score_data, name='api-score-data'),

    url(r'^unit/(?P<pk>\d+)/$', api_views.UnitDetail.as_view(), name='api-unit-detail'),
    url(r'^target/(?P<pk>\d+)/$', api_views.TargetDetail.as_view(), name='api-target-detail'),
    url(r'^timezero/(?P<pk>\d+)/$', api_views.TimeZeroDetail.as_view(), name='api-timezero-detail'),

    url(r'^model/(?P<pk>\d+)/$', api_views.ForecastModelDetail.as_view(), name='api-model-detail'),
    url(r'^model/(?P<pk>\d+)/forecasts/$', api_views.ForecastModelForecastList.as_view(), name='api-forecast-list'),

    url(r'^forecast/(?P<pk>\d+)/$', api_views.ForecastDetail.as_view(), name='api-forecast-detail'),
    url(r'^forecast/(?P<pk>\d+)/data/$', api_views.forecast_data, name='api-forecast-data'),
]
