import configparser
import datetime
import io
import itertools
import pathlib
import re
import textwrap
import unittest.mock

import pytest
import requests.exceptions
import requests_mock

import gtimelog2tick


def test_gtimelog2tick__parse_timelog__1():
    """It omits entries which do not match the requested projects."""
    entries = [
        gtimelog2tick.Entry(datetime.datetime(2014, 3, 31, 14, 48),
                            datetime.datetime(2014, 3, 31, 17, 10),
                            'proj2: maint: work'),
        gtimelog2tick.Entry(datetime.datetime(2014, 3, 31, 17, 48),
                            datetime.datetime(2014, 3, 31, 18, 10),
                            'proj3: dev: other work'),
        gtimelog2tick.Entry(datetime.datetime(2014, 3, 31, 18, 48),
                            datetime.datetime(2014, 3, 31, 19, 10),
                            'proj2: support: not working**'),
        gtimelog2tick.Entry(datetime.datetime(2014, 3, 31, 19, 48),
                            datetime.datetime(2014, 3, 31, 20, 10),
                            'proj2: meet: ABC-MISC'),
    ]
    config = {
        'requested_projects': ['proj2'],
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, [
                gtimelog2tick.Task('maintenance', 1),
                gtimelog2tick.Task('development', 2),
                gtimelog2tick.Task('support', 3),
                gtimelog2tick.Task('meeting', 4),
            ])
        ]
    }
    assert list(gtimelog2tick.parse_timelog(config, entries)) == [
        gtimelog2tick.WorkLog(entries[0], 'work', 'proj2: maintenance', 1),
        gtimelog2tick.WorkLog(entries[-1], 'ABC-MISC', 'proj2: meeting', 4),
    ]


def test_gtimelog2tick__parse_timelog__2():
    """It omits no entries if requested projects is empty."""
    entries = [
        gtimelog2tick.Entry(datetime.datetime(2014, 3, 31, 14, 48),
                            datetime.datetime(2014, 3, 31, 17, 10),
                            'proj2: maint: work'),
        gtimelog2tick.Entry(datetime.datetime(2014, 3, 31, 17, 48),
                            datetime.datetime(2014, 3, 31, 18, 10),
                            'proj3: dev: other'),
    ]
    config = {
        'requested_projects': [],
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, [
                gtimelog2tick.Task('maintenance', 1),
            ]),
            gtimelog2tick.Project('proj3', 43, [
                gtimelog2tick.Task('development', 5),
            ])
        ]
    }
    assert list(gtimelog2tick.parse_timelog(config, entries)) == [
        gtimelog2tick.WorkLog(entries[0], 'work', 'proj2: maintenance', 1),
        gtimelog2tick.WorkLog(entries[-1], 'other', 'proj3: development', 5),
    ]


class Route:

    def __init__(self, handler, params=None):
        self.handler = handler
        self.params = params or {}
        self.pattern = None


class TickApi:

    def __init__(self, mock, user='User Name'):
        self.mock = mock
        self.url = 'https://secure.tickspot.com'
        self.base = '/4711/api/v2'
        self.idseq = map(str, itertools.count(1))
        self.dtformat = '%Y-%m-%dT%H:%M:%S%z'
        self.routes = {
            r'get /projects.json\?page={page}': Route(self.list_projects, {
                'page': r'[0-9]+'
            }),
            'get /projects/{id}/tasks.json': Route(self.list_tasks, {
                'id': r'[0-9]+',
            }),
            r'get /users/{userid}/entries\.json\?start_date={start}'
            r'&end_date={end}': Route(self.list_entries, {
                'userid': r'[0-9]+',
                'start': r'[0-9]{4}-[0-9]{2}-[0-9]{2}',
                'end': r'[0-9]{4}-[0-9]{2}-[0-9]{2}',
            }),
            r'post /entries\.json': Route(self.add_entry),
            r'delete /entries/{id}.json': Route(self.delete_entry, {
                'id': r'[0-9]+',
            }),
        }
        for key, route in self.routes.items():
            method, path = key.split(None, 1)
            route.pattern = re.compile(self.base + path.format(**{
                k: '(?P<%s>%s)' % (k, v)
                for k, v in route.params.items()
            }))
            self.mock.register_uri(method, route.pattern, json=route.handler)

        self.db = {
            '2014-01-01': {'worklog': {}},
            '2014-02-01': {'worklog': {}},
            '2014-04-16': {'worklog': {}},
        }

        self._add_worklog(
            'Someone Else',
            datetime.datetime(
                2014,
                4,
                16,
                11,
                0).astimezone(),
            300,
            'did some work')

    def _get_url_params(self, request, name):
        return self.routes[name].pattern.search(request.url).groups()

    def _get_user_name(self):
        return '2411'

    def _datetime_to_isodate(self, dt):
        if isinstance(dt, str):
            dt = datetime.datetime.strptime(dt, self.dtformat)
        return dt.date().isoformat()

    def _add_worklog(self, user, started, seconds, comment):
        worklog_id = next(self.idseq)
        start_date = self._datetime_to_isodate(started)
        if start_date not in self.db:
            self.db[start_date] = {'worklog': {}}
        self.db[start_date]['worklog'][worklog_id] = {
            'id': worklog_id,
            'comment': comment,
            'started': started
            if isinstance(started, str)
            else started.strftime(self.dtformat),
            'timeSpent': round(seconds / 3600, 2),
            'timeSpentSeconds': seconds,
            'user': user
        }
        return worklog_id

    def list_projects(self, request, context):
        context.headers['content-type'] = 'application/json'
        page, = self._get_url_params(
            request, r'get /projects.json\?page={page}')

        if int(page) > 1:
            # We do not have multiple pages in the fixtures,
            # but as we pass the page parameter all the time
            # it should be fine.
            return []

        return [
            {
                "id": 2180739,
                "name": "project1-other",
                "budget": 255.0,
                "date_closed": None,
                "notifications": False,
                "billable": True,
                "recurring": False,
                "client_id": 400645,
                "owner_id": 289344,
                "url": "https://secure.tickspot.com/4711/api/v2/projects"
                "/2180739.json",
                "created_at": "2021-12-17T05:01:34.000-05:00",
                "updated_at": "2022-12-20T09:17:35.000-05:00"
            }, {
                "id": 2180740,
                "name": "project2",
                "budget": 0.0,
                "date_closed": None,
                "notifications": False,
                "billable": True,
                "recurring": False,
                "client_id": 400645,
                "owner_id": 289344,
                "url": "https://secure.tickspot.com/4711/api/v2/projects"
                "/2180740.json",
                "created_at": "2021-12-17T05:01:34.000-05:00",
                "updated_at": "2022-12-20T09:17:35.000-05:00"
            }, {
                "id": 2180741,
                "name": "project1-main",
                "budget": 0.0,
                "date_closed": None,
                "notifications": False,
                "billable": True,
                "recurring": False,
                "client_id": 400645,
                "owner_id": 289344,
                "url": "https://secure.tickspot.com/4711/api/v2/projects"
                "/2180741.json",
                "created_at": "2021-12-17T05:01:34.000-05:00",
                "updated_at": "2022-12-20T09:17:35.000-05:00"
            },
        ]

    def list_tasks(self, request, context):
        context.headers['content-type'] = 'application/json'
        id, = self._get_url_params(request, 'get /projects/{id}/tasks.json')

        return [
            {
                "id": 17880422,
                "name": "development",
                "budget": None,
                "position": 1,
                "project_id": id,
                "date_closed": None,
                "billable": True,
                "url": "https://secure.tickspot.com/4711/api/v2/tasks"
                "/17880422.json",
                "created_at": "2023-02-08T07:50:17.000-05:00",
                "updated_at": "2023-11-27T04:18:19.000-05:00"
            },
            {
                "id": 17882758,
                "name": "maintenance",
                "budget": None,
                "position": 2,
                "project_id": id,
                "date_closed": None,
                "billable": True,
                "url": "https://secure.tickspot.com/4711/api/v2/tasks"
                "/17882758.json",
                "created_at": "2023-02-09T11:01:20.000-05:00",
                "updated_at": "2023-11-27T04:18:08.000-05:00"
            },
            {
                "id": 17882759,
                "name": "support",
                "budget": None,
                "position": 3,
                "project_id": id,
                "date_closed": None,
                "billable": True,
                "url": "https://secure.tickspot.com/4711/api/v2/tasks"
                "/17882759.json",
                "created_at": "2023-02-09T11:01:20.000-05:00",
                "updated_at": "2023-11-27T04:18:08.000-05:00"
            },
        ]

    def list_entries(self, request, context):
        context.headers['content-type'] = 'application/json'
        userid, start, end = self._get_url_params(
            request,
            r'get /users/{userid}/entries\.json\?start_date={start}'
            r'&end_date={end}')
        logs = {id: worklog for date, data in self.db.items()
                for id, worklog in data['worklog'].items()
                if date == start
                and worklog.get('user') == self._get_user_name()}
        return [
            {
                "id": id,
                "date": start,
                "hours": log['timeSpent'],
                "notes": log['comment'],
                "task_id": 17880422,
                "user_id": userid,
                "url": f"https://secure.tickspot.com/4711/api/v2/entries/"
                f"{id}.json",
                "locked": False,
                "created_at": "2023-11-27T03:03:47.000-05:00",
                "updated_at": "2023-11-27T03:03:47.000-05:00"
            } for id, log in logs.items()
        ]

    def delete_entry(self, request, context):
        context.headers['content-type'] = 'application/json'
        context.status_code = 204
        id, = self._get_url_params(request, 'delete /entries/{id}.json')

        db = {}
        for date, data in self.db.items():
            db[date] = {}
            for key, value in data.items():
                assert key == 'worklog'
                db[date][key] = {}
                for k, v in value.items():
                    if k == id:
                        continue
                    db[date][key][k] = v
        self.db = db

    def add_entry(self, request, context):
        context.headers['content-type'] = 'application/json'

        context.status_code = 201
        data = request.json()
        day = self._datetime_to_isodate(data['date'])
        worklog_id = self._add_worklog(
            self._get_user_name(),
            data['date'],
            data['hours'] * 3600,
            data['notes'])
        worklog = self.db[day]['worklog'][worklog_id]
        return {
            "id": worklog_id,
            "date": day,
            "hours": worklog['timeSpent'],
            "notes": worklog['comment'],
            "task_id": data['task_id'],
            "user_id": self._get_user_name(),
            "url": f"https://secure.tickspot.com/4711/api/v2/entries"
            f"/{worklog_id}.json",
            "locked": False,
            "created_at": "2023-11-27T05:02:41.000-05:00",
            "updated_at": "2023-11-27T05:02:41.000-05:00"
        }


class Env:

    def __init__(self, path, mocker, tick):
        self.stdout = None
        self.path = pathlib.Path(path)
        self.gtimelogrc = path / 'gtimelogrc'
        self.timelog = path / 'timelog.txt'
        self.ticklog = path / 'tick.log'
        self.tick = tick

        config = configparser.ConfigParser()
        config.read_dict({
            'gtimelog2tick': {
                'subscription_id': 4711,
                'token': '<TICK-API-TOKEN>',
                'user_id': 2411,
                'email': 'tick@example.com',
                'timelog': str(self.timelog),
                'ticklog': str(self.ticklog),
                'projects': 'project1 project2',
            },
            'gtimelog': {},
        })
        with self.gtimelogrc.open('w') as f:
            config.write(f)

        self.log([
            '2014-03-24 14:15: arrived',
            '2014-03-24 18:14: project1-other: dev: some work',
            '',
            '2014-03-31 08:00: arrived'
            '2014-03-31 15:48: project1-main: dev: some work',
            '2014-03-31 17:10: project2: dev: some work',
            '2014-03-31 17:38: project1-main: dev: some work',
            '2014-03-31 18:51: project1-other: maint: more work'
            '',
            '2014-04-01 13:54: arrived',
            '2014-04-01 15:41: project1-other: dev: some work',
            '2014-04-01 16:04: tea **',
            '2014-04-01 18:00: project1-other: maint: more work',
            '',
            '2014-04-16 10:30: arrived',
            '2014-04-16 11:25: project1-other: dev: init work',
            '2014-04-16 12:30: project1-other: support: miss. issue',
        ])

    def log(self, lines):
        with self.timelog.open('a') as f:
            for line in lines:
                f.write(line + '\n')

    def run(self, argv=None):
        self.stdout = io.StringIO()
        argv = ['-c', str(self.gtimelogrc)] + (argv or [])
        return gtimelog2tick.main(argv, self.stdout)

    def get_worklog(self):
        username = self.tick._get_user_name()
        return [
            (worklog['started'], worklog['timeSpent'],
             worklog['comment'])
            for date, data in self.tick.db.items()
            for worklog_id, worklog in data['worklog'].items()
            if worklog['user'] == username
        ]

    def get_ticklog(self):
        with self.ticklog.open() as f:
            return [tuple(line.strip().split(',', 7)[1:]) for line in f]

    def get_stdout(self):
        return self.stdout.getvalue().splitlines()


@pytest.fixture
def env(tmpdir, mocker):
    with requests_mock.Mocker() as mock:
        tick = TickApi(mock)
        yield Env(tmpdir, mocker, tick)


def test_no_args(env, mocker):
    mocker.patch('gtimelog2tick.get_now',
                 return_value=datetime.datetime(2014, 4, 18).astimezone())
    assert env.run() is None
    env.log([
        '',
        '2014-04-17 10:30: arrived',
        '2014-04-17 12:25: project1-main: dev: do more work',
    ])
    assert env.run() is None
    assert env.get_worklog() == [
        ('2014-04-16T10:30:00+02:00', 0.92, 'init work'),
        ('2014-04-16T11:25:00+02:00', 1.08, 'miss. issue'),
        ('2014-04-17T10:30:00+02:00', 1.92, 'do more work'),
    ]
    assert env.get_ticklog() == [
        ('2014-04-16T10:30+02:00', '0.92', '2', 'add', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '3', 'add', 'miss. issue'),
        ('2014-04-16T00:00', '0.92', '2', 'delete', 'init work'),
        ('2014-04-16T00:00', '1.08', '3', 'delete', 'miss. issue'),
        ('2014-04-16T10:30+02:00', '0.92', '4', 'add', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '5', 'add', 'miss. issue'),
        ('2014-04-17T10:30+02:00', '1.92', '6', 'add', 'do more work'),
    ]


def test_gtimelog2tick__parse_timelog__3(env, mocker):
    """It raises a DataError for an entry with negative time."""
    mocker.patch('gtimelog2tick.get_now',
                 return_value=datetime.datetime(2023, 12, 7).astimezone())
    assert env.run() is None
    env.log([
        '',
        '2023-12-07 10:30: arrived',
        '2023-12-07 12:25: project1-main: dev: work1.1',
        '2023-12-07 12:24: project2: dev: more work2.1 - negative time',
        '2023-12-07 15:24: project1-main: dev: work1.2',
    ])
    with pytest.raises(gtimelog2tick.DataError) as err:
        assert env.run()
    err.match(r'Negative hours: WorkLog\(.*')


def test_gtimelog2tick__parse_timelog__4(env, mocker):
    """It it ignores entries with zero minutes duration."""
    mocker.patch('gtimelog2tick.get_now',
                 return_value=datetime.datetime(2023, 12, 7).astimezone())
    assert env.run() is None
    env.log([
        '',
        '2023-12-07 10:30: arrived',
        '2023-12-07 12:25: project1-main: dev: work1.1',
        '2023-12-07 12:25: project2: dev: more work2.1 - no time',
        '2023-12-07 15:24: project1-main: dev: work1.2',
    ])
    assert env.run() is None
    assert env.get_worklog() == [
        ('2023-12-07T10:30:00+01:00', 1.92, 'work1.1'),
        ('2023-12-07T12:25:00+01:00', 2.98, 'work1.2'),
    ]
    assert env.get_ticklog() == [
        ('2023-12-07T10:30+01:00', '1.92', '2', 'add', 'work1.1'),
        ('2023-12-07T12:25+01:00', '2.98', '3', 'add', 'work1.2'),
    ]


def test_full_sync(env):
    assert env.run(['--since', '2014-01-01']) is None
    env.log([
        '',
        '2014-04-17 10:30: arrived',
        '2014-04-17 13:25: project1-main: dev: do more work',
        '2014-04-17T15:25: project1-main: dev: broken entry',
    ])
    assert env.run(['--since', '2014-01-01']) is None
    assert env.get_worklog() == [
        ('2014-04-16T10:30:00+02:00', 0.92, 'init work'),
        ('2014-04-16T11:25:00+02:00', 1.08, 'miss. issue'),
        ('2014-03-24T14:15:00+01:00', 3.98, 'some work'),
        ('2014-03-31T08:00:00+02:00', 9.17, 'some work'),
        ('2014-03-31T17:10:00+02:00', 0.47, 'some work'),
        ('2014-03-31T17:38:00+02:00', 1.22, 'more work'),
        ('2014-04-01T13:54:00+02:00', 1.78, 'some work'),
        ('2014-04-01T16:04:00+02:00', 1.93, 'more work'),
        ('2014-04-17T10:30:00+02:00', 2.92, 'do more work'),
    ]
    assert env.get_ticklog() == [
        ('2014-03-24T14:15+01:00', '3.98', '2', 'add', 'some work'),
        ('2014-03-31T08:00+02:00', '9.17', '3', 'add', 'some work'),
        ('2014-03-31T17:10+02:00', '0.47', '4', 'add', 'some work'),
        ('2014-03-31T17:38+02:00', '1.22', '5', 'add', 'more work'),
        ('2014-04-01T13:54+02:00', '1.78', '6', 'add', 'some work'),
        ('2014-04-01T16:04+02:00', '1.93', '7', 'add', 'more work'),
        ('2014-04-16T10:30+02:00', '0.92', '8', 'add', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '9', 'add', 'miss. issue'),
        ('2014-03-24T00:00', '3.98', '2', 'delete', 'some work'),
        ('2014-03-24T14:15+01:00', '3.98', '10', 'add', 'some work'),
        ('2014-03-31T00:00', '9.17', '3', 'delete', 'some work'),
        ('2014-03-31T00:00', '0.47', '4', 'delete', 'some work'),
        ('2014-03-31T00:00', '1.22', '5', 'delete', 'more work'),
        ('2014-03-31T08:00+02:00', '9.17', '11', 'add', 'some work'),
        ('2014-03-31T17:10+02:00', '0.47', '12', 'add', 'some work'),
        ('2014-03-31T17:38+02:00', '1.22', '13', 'add', 'more work'),
        ('2014-04-01T00:00', '1.78', '6', 'delete', 'some work'),
        ('2014-04-01T00:00', '1.93', '7', 'delete', 'more work'),
        ('2014-04-01T13:54+02:00', '1.78', '14', 'add', 'some work'),
        ('2014-04-01T16:04+02:00', '1.93', '15', 'add', 'more work'),
        ('2014-04-16T00:00', '0.92', '8', 'delete', 'init work'),
        ('2014-04-16T00:00', '1.08', '9', 'delete', 'miss. issue'),
        ('2014-04-16T10:30+02:00', '0.92', '16', 'add', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '17', 'add', 'miss. issue'),
        ('2014-04-17T10:30+02:00', '2.92', '18', 'add', 'do more work'),
    ]


def test_since_date(env):
    assert env.run(['--since', '2014-04-16']) is None
    assert env.run(['--since', '2014-04-16']) is None
    assert env.get_worklog() == [
        ('2014-04-16T10:30:00+02:00', 0.92, 'init work'),
        ('2014-04-16T11:25:00+02:00', 1.08, 'miss. issue'),
    ]
    assert env.get_ticklog() == [
        ('2014-04-16T10:30+02:00', '0.92', '2', 'add', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '3', 'add', 'miss. issue'),
        ('2014-04-16T00:00', '0.92', '2', 'delete', 'init work'),
        ('2014-04-16T00:00', '1.08', '3', 'delete', 'miss. issue'),
        ('2014-04-16T10:30+02:00', '0.92', '4', 'add', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '5', 'add', 'miss. issue'),
    ]


def test_until_date(env):
    assert env.run(['--until', '2014-03-25', '--since', '2014-03-21']) is None
    assert env.get_worklog() == [
        ('2014-03-24T14:15:00+01:00', 3.98, 'some work')
    ]
    assert env.get_ticklog() == [
        ('2014-03-24T14:15+01:00', '3.98', '2', 'add', 'some work')
    ]


def test_dry_run(env):
    env.tick._add_worklog(
        env.tick._get_user_name(),
        '2014-04-16T10:30:00+02:00', 3800, 'do more work')

    assert env.run(
        ['--dry-run', '--since', '2014-01-01']) is None, env.get_stdout()
    assert env.get_worklog() == [
        ('2014-04-16T10:30:00+02:00', 1.06, 'do more work')
    ]
    assert env.get_ticklog() == [
        ('2014-03-24T14:15+01:00', '3.98', '', 'add (dry run)', 'some work'),
        ('2014-03-31T08:00+02:00', '9.17', '', 'add (dry run)', 'some work'),
        ('2014-03-31T17:10+02:00', '0.47', '', 'add (dry run)', 'some work'),
        ('2014-03-31T17:38+02:00', '1.22', '', 'add (dry run)', 'more work'),
        ('2014-04-01T13:54+02:00', '1.78', '', 'add (dry run)', 'some work'),
        ('2014-04-01T16:04+02:00', '1.93', '', 'add (dry run)', 'more work'),
        ('2014-04-16T00:00', '1.06', '', 'delete (dry run)', 'do more work'),
        ('2014-04-16T10:30+02:00', '0.92', '', 'add (dry run)', 'init work'),
        ('2014-04-16T11:25+02:00', '1.08', '', 'add (dry run)', 'miss. issue'),
    ]
    assert env.get_stdout() == [
        '',
        'ADD: 2014-03-24T14:15+01:00     3.98: some work',
        'ADD: 2014-03-31T08:00+02:00     9.17: some work',
        'ADD: 2014-03-31T17:10+02:00     0.47: some work',
        'ADD: 2014-03-31T17:38+02:00     1.22: more work',
        'ADD: 2014-04-01T13:54+02:00     1.78: some work',
        'ADD: 2014-04-01T16:04+02:00     1.93: more work',
        'ADD: 2014-04-16T10:30+02:00     0.92: init work',
        'ADD: 2014-04-16T11:25+02:00     1.08: miss. issue',
        '',
        'TOTALS:',
        'project1-main: development: 0.47 h in 1 entries.',
        'project1-other: development: 6.68 h in 3 entries.',
        'project1-other: maintenance: 3.15 h in 2 entries.',
        'project1-other: support: 1.08 h in 1 entries.',
        'project2: development: 9.17 h in 1 entries.',
    ]


def test_gtimelog2tick__parse_entry_message__1(env):
    """In case of multiple project matches it prefers the exact one."""
    config = {
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, [
                gtimelog2tick.Task('dev', 1),
            ]),
            gtimelog2tick.Project('proj2 - maintenance', 43, [])
        ]
    }
    task, text, task_id = gtimelog2tick.parse_entry_message(
        config, 'proj2: dev: work')
    assert task == 'proj2: dev'
    assert text == 'work'
    assert task_id == 1


def test_gtimelog2tick__parse_entry_message__2(env):
    """It raises a DataError if no matching project can be found."""
    config = {
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, []),
        ]
    }
    with pytest.raises(gtimelog2tick.DataError) as err:
        task, text, task_id = gtimelog2tick.parse_entry_message(
            config, 'proj1: dev: work')
    assert err.match('Cannot find a Tick project matching proj1: dev: work.')


def test_gtimelog2tick__parse_entry_message__3(env):
    """It raises a DataError in case of multiple non-exact project matches."""
    config = {
        'tick_projects': [
            gtimelog2tick.Project('proj2 - dev', 42, []),
            gtimelog2tick.Project('proj2 - maintenance', 43, [])
        ]
    }
    with pytest.raises(gtimelog2tick.DataError) as err:
        task, text, task_id = gtimelog2tick.parse_entry_message(
            config, 'proj2: dev: work')
    assert err.match(
        r"Found multiple Tick projects matching 'proj2: dev: work', but no "
        r"exact match. \(proj2 - dev, proj2 - maintenance\)")


def test_gtimelog2tick__parse_entry_message__4(env):
    """In case of multiple task matches it prefers the exact one."""
    config = {
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, [
                gtimelog2tick.Task('dev', 1),
                gtimelog2tick.Task('dev - 2', 1),
                gtimelog2tick.Task('dev - 23', 1),
            ]),
        ]
    }
    task, text, task_id = gtimelog2tick.parse_entry_message(
        config, 'proj2: dev: work')
    assert task == 'proj2: dev'
    assert text == 'work'
    assert task_id == 1


def test_gtimelog2tick__parse_entry_message__5(env):
    """It raises a DataError if no matching task can be found."""
    config = {
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, [
                gtimelog2tick.Task('dev', 1),
            ]),
        ]
    }
    with pytest.raises(gtimelog2tick.DataError) as err:
        task, text, task_id = gtimelog2tick.parse_entry_message(
            config, 'proj2: support: work')
    assert err.match("Cannot find a Tick task matching proj2: support: work.")


def test_gtimelog2tick__parse_entry_message__6(env):
    """It raises a DataError in case of multiple non-exact task matches."""
    config = {
        'tick_projects': [
            gtimelog2tick.Project('proj2', 42, [
                gtimelog2tick.Task('dev - 2', 1),
                gtimelog2tick.Task('dev - 23', 1),
            ]),
        ]
    }
    with pytest.raises(gtimelog2tick.DataError) as err:
        task, text, task_id = gtimelog2tick.parse_entry_message(
            config, 'proj2: dev: work')
    assert err.match(
        r"Found multiple Tick tasks matching 'proj2: dev: work', but no exact"
        r" match. \(dev - 2, dev - 23\)")


def test_gtimelog2tick__read_config__1(tmpdir):
    """It renders an exception if the config files does not exist."""
    path = pathlib.Path(tmpdir) / 'i-do-not-exist.ini'
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('i-do-not-exist.ini does not exist.')


def test_gtimelog2tick__read_config__2(tmpdir):
    """It renders an exception if the section gtimelog2tick does not exist."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.touch()
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match(r'Section \[gtimelog2tick\] is not present')


def test_gtimelog2tick__read_config__4(tmpdir):
    """It renders an exception if subscription_id is missing."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.write_text(textwrap.dedent("""\
        [gtimelog2tick]"""))
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('The Tick subscription id is not specified, set it via'
                     ' the gtimelog2tick.subscription_id setting.')


def test_gtimelog2tick__read_config__5(tmpdir):
    """It renders an exception if token is missing."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.write_text(textwrap.dedent("""\
        [gtimelog2tick]
        subscription_id = 123"""))
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('The Tick API token is not specified, set it via the'
                     ' gtimelog2tick.token setting.')


def test_gtimelog2tick__read_config__6(tmpdir):
    """It renders an exception if user_id is missing."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.write_text(textwrap.dedent("""\
        [gtimelog2tick]
        subscription_id = 123
        token = <TOKEN>"""))
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('The Tick user ID is not specified, set it via the'
                     ' gtimelog2tick.user_id setting.')


def test_gtimelog2tick__read_config__7(tmpdir):
    """It renders an exception if email is missing."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.write_text(textwrap.dedent("""\
        [gtimelog2tick]
        subscription_id = 123
        token = <TOKEN>
        user_id = 456"""))
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('Your email address is not specified, set it via the'
                     ' gtimelog2tick.email setting.')


def test_gtimelog2tick__read_config__9(tmpdir):
    """It renders an exception if timelog file does not exist."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.write_text(textwrap.dedent("""\
        [gtimelog2tick]
        subscription_id = 123
        token = <TOKEN>
        user_id = 456
        email = test@example.com"""))
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('Timelog file .*/timelog.txt does not exist.')


def test_gtimelog2tick__read_config__10(tmpdir):
    """It renders an exception if ticklog file ist nor writeable."""
    path = pathlib.Path(tmpdir) / 'config.ini'
    path.write_text(textwrap.dedent("""\
        [gtimelog2tick]
        subscription_id = 123
        token = <TOKEN>
        user_id = 456
        email = test@example.com"""))
    (pathlib.Path(tmpdir) / 'timelog.txt').touch()
    ticklog = pathlib.Path(tmpdir) / 'ticklog.txt'
    ticklog.touch()
    ticklog.chmod(0o000)
    with pytest.raises(gtimelog2tick.ConfigurationError) as err:
        gtimelog2tick.read_config(path)
    assert err.match('Tick log file .*/ticklog.txt is not writable:')


def test_gtimelog2tick__call__1():
    """It retries HTTP calls as Tick's API is flaky."""
    session_mock = unittest.mock.Mock()
    session_mock.get = unittest.mock.Mock(
        side_effect=requests.exceptions.ConnectionError)
    config = {
        'session': session_mock,
        'email': 'test@examle.com',
        'token': '<TOKEN>',
        'api': 'https://example.com/tick',
    }
    with pytest.raises(requests.exceptions.ConnectionError):
        gtimelog2tick.call(config, 'get', '/some/path')


def test_gtimelog2tick__call__2():
    """It raises CommunicationError, if return codes do not match."""
    response_mock = unittest.mock.Mock()
    response_mock.status_code = 500
    response_mock.text = 'Internal ServerError'
    session_mock = unittest.mock.Mock()
    session_mock.get = unittest.mock.Mock(return_value=response_mock)
    config = {
        'session': session_mock,
        'email': 'test@examle.com',
        'token': '<TOKEN>',
        'api': 'https://example.com/tick',
    }
    with pytest.raises(gtimelog2tick.CommunicationError) as err:
        gtimelog2tick.call(config, 'get', '/some/path')
    assert err.match(r'Error 500 expected \{200\}: Internal ServerError')


def test_gtimelog2tick__Date____call____1(mocker):
    """It parses `today` as the current day at midnight."""
    mocker.patch('gtimelog2tick.get_now',
                 return_value=datetime.datetime(2014, 4, 18, 17).astimezone())
    date = gtimelog2tick.Date()('today')
    assert date == datetime.datetime(2014, 4, 18, 0, 0).astimezone()


def test_gtimelog2tick__Date____call____2(mocker):
    """It parses `yesterday` as the previous day at midnight."""
    mocker.patch('gtimelog2tick.get_now',
                 return_value=datetime.datetime(2014, 4, 18, 17).astimezone())
    date = gtimelog2tick.Date()('yesterday')
    assert date == datetime.datetime(2014, 4, 17, 0, 0).astimezone()


def test_gtimelog2tick__Date___main__1(capsys):
    """It raises SystemExit if `until` is before `since`."""
    with pytest.raises(SystemExit):
        gtimelog2tick._main(['--since', '2023-09-09', '--until', '2023-08-08'])
    captured = capsys.readouterr()
    assert 'the time interval is empty' in captured.err


def test_gtimelog2tick__Date___main__2(tmpdir):
    """It returns 1 and print a message on error during reading config file."""
    path = pathlib.Path(tmpdir) / 'i-do-not-exist.ini'
    stdout = io.StringIO()
    assert gtimelog2tick._main(['--config', str(path)], stdout) == 1
    assert stdout.getvalue().startswith('Error: Configuration file')
    assert stdout.getvalue().endswith('does not exist.\n')
