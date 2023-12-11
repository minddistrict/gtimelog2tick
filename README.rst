.. default-role:: literal

Synchronize gTimeLog to Tick
############################

.. image:: https://github.com/minddistrict/gtimelog2tick/actions/workflows/build.yml/badge.svg
    :target: https://github.com/minddistrict/gtimelog2tick/actions/workflows/build.yml


This script will read your `timelog.txt` file populated by gtimelog_ and will
submit work log entries to Tick, via `Tick API`_.

It is based on https://github.com/ProgrammersOfVilnius/gtimelog2jira.

Installation
============

Installation with pipx
++++++++++++++++++++++

.. code-block:: sh

  pipx install .

Usage
=====

In order to synchronize your most recent entries to Tick, simply run::

  gtimelog2tick

By default, this command will synchronize entries created 7 days ago up to now.

You can control what time period you want to synchronize using the `--since`
and/or `--until` parameters::

  gtimelog2tick --since 2023-12-01 --until 2023-12-24

If you want to test things, without creating work log entries in Tick, you
can use the `--dry-run` flag::

  gtimelog2tick --dry-run

This way nothing will be sent to Tick, the script will instead show what it would do.


Configuration
=============

By default, `gtimelog2tick` reads configuration from the `~/.gtimelog/gtimelogrc`
file. Configuration file example:

.. code-block:: ini

  [gtimelog2tick]
  subscription_id = 234234
  token = a343jk34s
  user_id = 12323
  email = user@example.com
  timelog = ~/.gtimelog/timelog.txt
  ticklog = ~/.gtimelog/tick.log
  projects =
    FOO
    BAR
    BAZ

Use the `Subscription ID` as value for the `subscription_id` field and the `API
Token` for the `token` field. Both can be looked up at your personal profile
page in the web UI.

The value for `user_id` can be found in the URL of the personal profile page.
It is the number between `users/` and `/edit`.

Use your actual email address as otherwise requests might be refused.

`timelog` is the path where the gtimelog time log file is stored. The default
should be okay.
`ticklog`: this file is used to write a line for each action which is done via
the Tick API. When using `--dry-run`, this file is also filled.

`projects` option should list all project prefixes for upload. These prefixes
will be used to identify tick projects. If the script for an entry does not
find a matching tick project, it will skip that entry. **Caution:** This option
can be empty or omitted to upload all projects.

.. _gtimelog: https://gtimelog.org/
.. _Tick API: https://github.com/tick/tick-api/tree/master
