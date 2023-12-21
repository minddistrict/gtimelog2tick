#!/usr/bin/env python3
import argparse
import collections
import configparser
import dataclasses
import datetime
import itertools
import pathlib
import sys
from typing import Iterable, Optional

import requests

assert sys.version_info >= (3, 11), "You need Python 3.11 or newer"  # nosec


Entry = collections.namedtuple('Entry', ('start', 'end', 'message'))
JiraWorkLog = collections.namedtuple('JiraWorkLog', ('id', 'start', 'end'))
TickSyncStatus = collections.namedtuple(
    'TickSyncStatus', ('worklog', 'json', 'action'))


@dataclasses.dataclass
class Task:
    name: str
    id: int
    project: Optional['Project'] = dataclasses.field(default=None, init=False)

    @property
    def title(self) -> str:
        project_name = (
            '<unknown project>' if self.project is None else self.project.name)
        return f'{project_name}: {self.name}'


@dataclasses.dataclass
class Project:
    name: str
    id: int
    _tasks: tuple[Task] = ()

    def __post_init__(self) -> None:
        # Set the project for the tasks set during __init__.
        self.tasks = self._tasks

    @property
    def tasks(self) -> tuple[Task]:
        return self._tasks

    @tasks.setter
    def tasks(self, value: tuple[Task]) -> None:
        self._tasks = value
        for task in value:
            task.project = self


@dataclasses.dataclass
class WorkLog:
    """Entry in the work log."""

    entry: Entry
    config: dict
    _text: str | None = dataclasses.field(default=None, init=False)
    _task: Task | None = dataclasses.field(default=None, init=False)

    def __post_init__(self) -> None:
        self.start = self.entry.start
        self.end = self.entry.end
        self.hours = round(
            int((self.entry.end - self.entry.start).total_seconds()) / 3600,
            2)

    @property
    def text(self) -> str:
        if self._text is None:
            self._parse_entry_message()
        return self._text

    @text.setter
    def text(self, value: str) -> None:
        self._text = value

    @property
    def task(self) -> Task:
        if self._task is None:
            self._parse_entry_message()
        return self._task

    @task.setter
    def task(self, value: Task) -> None:
        """Create an arbitrary task without parsing the entry message."""
        self._task = value

    def _parse_entry_message(self) -> None:
        """Parse entry message into task and text and store them."""
        msg = self.entry.message
        try:
            project_name, task_name, *text_parts = msg.split(':')
        except ValueError:
            raise DataError(
                f'Error: Unable to split {msg!r}, it needs one colon or more.')

        task_name = task_name.strip()
        tick_projects = [
            (x, x.name == project_name)
            for x in self.config['tick_projects']
            if x.name.startswith(project_name)]

        if not tick_projects:
            raise DataError(f'Cannot find a Tick project for {msg!r}.')
        if len(tick_projects) > 1:
            exact_match = [x for x, match in tick_projects if match]
            if not exact_match:
                raise DataError(
                    f'Found multiple Tick projects for {msg!r}, but no'
                    ' exact match.'
                    f' ({", ".join(x[0].name for x in tick_projects)})')
            tick_project = exact_match[0]
        else:
            tick_project = tick_projects[0][0]
        if not tick_project.tasks:
            raw_tasks = call(
                self.config, 'get', f'/projects/{tick_project.id}/tasks.json')
            tick_project.tasks = tuple(
                Task(x['name'], x['id']) for x in raw_tasks)

        possible_tasks = [
            x
            for x in tick_project.tasks
            if x.name.startswith(task_name)]

        if not possible_tasks:
            raise DataError(f'Cannot find a Tick task for {msg!r}.')
        if len(possible_tasks) > 1:
            exact_match = [
                task for task in possible_tasks if task.name == task_name]
            if not exact_match:
                raise DataError(
                    f'Found multiple Tick tasks for {msg!r}, but no'
                    ' exact match.'
                    f' ({", ".join(x.name for x in tick_project.tasks)})')
            task = exact_match[0]
        else:
            task = possible_tasks[0]

        self._task = task
        self._text = ':'.join(text_parts).strip()


class ConfigurationError(Exception):
    pass


class CommunicationError(Exception):
    pass


class DataError(Exception):
    """There is an error in the logged data."""


def read_config(config_file: pathlib.Path) -> dict:
    if not config_file.exists():
        raise ConfigurationError(
            f"Configuration file {config_file} does not exist.")

    config = configparser.ConfigParser()
    config.optionxform = str  # do not lowercase the aliases section!
    config.read(config_file)

    if not config.has_section('gtimelog2tick'):
        raise ConfigurationError(
            f"Section [gtimelog2tick] is not present in {config_file} config"
            " file.")

    subscription_id = config['gtimelog2tick'].get('subscription_id')
    token = config['gtimelog2tick'].get('token')
    user_id = config['gtimelog2tick'].get('user_id')
    email = config['gtimelog2tick'].get('email')
    timelog = config['gtimelog2tick'].get('timelog')
    ticklog = config['gtimelog2tick'].get('ticklog')
    requested_projects = config.get('gtimelog2tick', 'projects', fallback='')
    midnight = config.get('gtimelog', 'virtual_midnight', fallback='06:00')

    if not subscription_id:
        raise ConfigurationError(
            "The Tick subscription id is not specified, set it via the"
            " gtimelog2tick.subscription_id setting. Take its value from your"
            " profile page on tickspot.com.")
    url = f'https://secure.tickspot.com/{subscription_id}/api/v2'

    if not token:
        raise ConfigurationError(
            "The Tick API token is not specified, set it via the"
            " gtimelog2tick.token setting. Take its value from your profile"
            " page on tickspot.com.")

    if not user_id:
        raise ConfigurationError(
            "The Tick user ID is not specified, set it via the"
            " gtimelog2tick.user_id setting. Take its value from the URL of"
            " your profile page on tickspot.com.")

    if not email:
        raise ConfigurationError(
            "Your email address is not specified, set it via the"
            " gtimelog2tick.email setting.")

    requested_projects = set(requested_projects.split())

    if not timelog:
        timelog = config_file.parent / 'timelog.txt'

    timelog = pathlib.Path(timelog).expanduser().resolve()
    if not timelog.exists():
        raise ConfigurationError(f"Timelog file {timelog} does not exist.")

    if not ticklog:
        ticklog = config_file.parent / 'ticklog.txt'
    ticklog = pathlib.Path(ticklog).expanduser().resolve()
    try:
        ticklog.open('a').close()
    except OSError as e:
        raise ConfigurationError(
            f"Tick log file {ticklog} is not writable: {e}.")

    session = requests.Session()

    config = {
        'api': url,
        'token': token,
        'user_id': user_id,
        'email': email,
        'timelog': timelog,
        'ticklog': ticklog,
        'requested_projects': requested_projects,
        'session': session,
        'midnight': midnight,
    }

    page = 1
    tick_projects = []
    while True:
        raw_projects = call(config, 'get', f'/projects.json?page={page}')
        if not raw_projects:
            break
        tick_projects.extend(
            [Project(x['name'], x['id']) for x in raw_projects])
        page += 1
    config['tick_projects'] = tick_projects
    return config


def read_timelog(
    f: Iterable[str],
    midnight='06:00'
) -> Iterable[Entry]:
    last = None
    nextday = None
    hour, minute = map(int, midnight.split(':'))
    midnight = {'hour': hour, 'minute': minute}
    day = datetime.timedelta(days=1)
    entries = 0
    last_note = None
    for line in f:
        line = line.strip()
        if line == '':
            continue

        try:
            time, note = line.split(': ', 1)
            time = datetime.datetime.strptime(
                time, '%Y-%m-%d %H:%M').astimezone()
        except ValueError:
            continue

        if nextday is None or time >= nextday:
            if last is not None and entries == 0:  # pragma: no cover
                yield Entry(last, last, last_note)
            entries = 0
            last = time
            last_note = note
            nextday = time.replace(**midnight)
            if time >= nextday:  # pragma: no cover
                nextday += day
            continue

        yield Entry(last, time, note)

        entries += 1
        last = time
        last_note = note

    if last is not None and entries == 0:  # pragma: no cover
        yield Entry(last, last, last_note)


def parse_timelog(
    config: dict,
    entries: Iterable[Entry],
) -> Iterable[WorkLog]:
    for entry in entries:
        # Skip all non-work related entries.
        if entry.message.endswith('**'):
            continue
        # Skip all lines which do not match the requested projects if requested
        # projects are specified
        if config['requested_projects']:
            if not any(entry.message.startswith(x)
                       for x in config['requested_projects']):
                continue

        worklog = WorkLog(entry, config)
        if worklog.hours > 0:
            yield worklog
        elif worklog.hours < 0:
            raise DataError(f'Negative hours: {worklog}')


def get_now():
    return datetime.datetime.now().astimezone()


def filter_timelog(
        entries: Iterable[WorkLog],
        *,
        since=None,
        until=None) -> Iterable[WorkLog]:
    if since is None:
        since = get_now() - datetime.timedelta(days=7)

    for entry in entries:
        if since and entry.start < since:
            continue
        if until and entry.end > until:
            continue
        yield entry


def call(
    config: dict,
    verb: str,
    path: str,
    expected_status_codes: set[int] = {200},
    data: dict | None = None,
) -> dict | None:
    caller = getattr(config['session'], verb)
    headers = {'content-type': 'application/json; charset=utf-8',
               'user-agent': f'gtimelog2tick ({config["email"]})',
               'authorization': f'Token token={config["token"]}'}
    kwargs = {
        'url': f'{config["api"]}{path}',
        'headers': headers}
    if data:
        kwargs['json'] = data
    err = None
    for _ in range(10):
        try:
            response = caller(**kwargs)
        except requests.exceptions.ConnectionError as e:
            err = e
            continue
        else:
            break
    else:
        raise err

    if response.status_code not in expected_status_codes:
        raise CommunicationError(
            f'Error {response.status_code} expected {expected_status_codes}:'
            f' {response.text}')
    if verb == 'delete':
        return ''
    return response.json()


def remove_tick_data(
    config: dict,
    date: datetime.date,
    dry_run: bool
) -> Iterable[TickSyncStatus]:
    """Remove pre-existing data in tick."""
    get_path = (
        f'/users/{config["user_id"]}/entries.json'
        f'?start_date={date.isoformat()}'
        f'&end_date={date.isoformat()}'
    )
    entries = call(config, 'get', get_path)
    for entry in entries:
        date = datetime.datetime.strptime(entry['date'], '%Y-%m-%d')
        del_worklog = WorkLog(
            Entry(date,
                  date + datetime.timedelta(hours=entry['hours']),
                  entry["id"]), config={})
        del_worklog.task = Task('<unknown task name>', entry["task_id"])
        del_worklog.text = entry["notes"]
        if dry_run:
            yield TickSyncStatus(del_worklog, {}, 'delete (dry run)')
        else:
            call(config, 'delete', f'/entries/{entry["id"]}.json', {204})
            yield TickSyncStatus(del_worklog, {"id": entry["id"]}, 'delete')


def add_tick_entry(
    config: dict,
    worklog: WorkLog,
    dry_run: bool,
) -> Iterable[TickSyncStatus]:
    """Add a new tick entry."""
    data = {
        "date": worklog.start.isoformat(),
        "hours": worklog.hours,
        "notes": worklog.text,
        "task_id": worklog.task.id,
        "user_id": config["user_id"],
    }
    if dry_run:
        yield TickSyncStatus(worklog, data, 'add (dry run)')
    else:
        response = call(config, 'post', '/entries.json', {201}, data=data)
        yield TickSyncStatus(worklog, response, 'add')


def sync_with_tick(
        config,
        worklogs: Iterable[WorkLog],
        dry_run=False) -> Iterable[TickSyncStatus]:
    def get_day(entry):
        return entry.start.date()
    for date, worklogs in itertools.groupby(worklogs, key=get_day):
        yield from remove_tick_data(config, date, dry_run)
        for worklog in worklogs:
            yield from add_tick_entry(config, worklog, dry_run)


def log_tick_sync(
        tick_sync_status_items: Iterable[TickSyncStatus],
        ticklog) -> Iterable[TickSyncStatus]:
    with ticklog.open('a') as f:
        for worklog, resp, action in tick_sync_status_items:
            comment = worklog.text
            f.write(','.join(map(str, [
                get_now().isoformat(timespec='seconds'),
                worklog.start.isoformat(timespec='minutes'),
                worklog.hours,
                resp.get('id', ''),
                action,
                comment,
            ])) + '\n')

            yield TickSyncStatus(worklog, resp, action)


class Date:
    """Argparse type representing a date."""

    def __init__(self, fmt='%Y-%m-%d'):
        self.fmt = fmt

    def __call__(self, value):
        if value.lower() == 'today':
            return get_now().replace(
                hour=0, minute=0, second=0, microsecond=0)
        if value.lower() == 'yesterday':
            return (get_now() - datetime.timedelta(1)).replace(
                hour=0,
                minute=0,
                second=0,
                microsecond=0)
        return datetime.datetime.strptime(value, self.fmt).astimezone()


def show_results(
        tick_sync_status_items: Iterable[TickSyncStatus],
        stdout):
    totals = {
        'hours': collections.defaultdict(int),
        'entries': collections.defaultdict(int),
    }
    print(file=stdout)
    for worklog, resp, action in tick_sync_status_items:
        action = action.replace(' (dry run)', '')
        if action == 'add':
            print('ADD: {start} {amount:>8.2f}: {comment}'.format(
                start=worklog.start.isoformat(timespec='minutes'),
                amount=worklog.hours,
                comment=worklog.text,
            ), file=stdout)
            totals['hours'][worklog.task.title] += worklog.hours
            totals['entries'][worklog.task.title] += 1
    if totals['hours']:
        print(file=stdout)
        print('TOTALS:', file=stdout)
        for task, hours in sorted(totals['hours'].items()):
            num_entries = totals['entries'][task]
            print(f'{task}: {hours:.2f} h in {num_entries} entries.',
                  file=stdout)


def _main(argv=None, stdout=sys.stdout):
    parser = argparse.ArgumentParser(
        epilog='--since and --until also understand the arguments today as'
               ' well as yesterday')
    parser.add_argument(
        '-c', '--config', default='~/.gtimelog/gtimelogrc',
        help='path of the config file, defaults to ~/.gtimelog/gtimelogrc')
    parser.add_argument(
        '--dry-run',
        action='store_true',
        default=False,
        help="don't sync anything, just show what would be done")
    parser.add_argument(
        '--since', type=Date(),
        help="sync logs from specified yyyy-mm-dd date, defaults to today"
             " minus 7 days")
    parser.add_argument(
        '--until', type=Date(),
        help="sync logs until specified yyyy-mm-dd date, it does _not_"
             " include the specified day, there is no default.")
    args = parser.parse_args(argv)

    if args.since and args.until and args.since >= args.until:
        parser.error(
            f'the time interval is empty ({args.since} .. {args.until})')

    config_file = pathlib.Path(args.config).expanduser().resolve()
    try:
        config = read_config(config_file)
    except ConfigurationError as e:
        print('Error:', e, file=stdout)
        return 1

    with config['timelog'].open() as f:
        entries = read_timelog(f, midnight=config['midnight'])
        entries = parse_timelog(config, entries)
        entries = filter_timelog(entries, since=args.since, until=args.until)
        entries = sync_with_tick(config, entries, dry_run=args.dry_run)
        entries = log_tick_sync(entries, config['ticklog'])
        show_results(entries, stdout)


def main(argv=None, stdout=sys.stdout):
    try:
        return _main(argv=argv, stdout=stdout)
    except KeyboardInterrupt:  # pragma: no cover
        sys.exit("Interrupted!")


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())
