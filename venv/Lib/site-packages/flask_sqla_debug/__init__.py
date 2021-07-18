"""
Implement debug for flask + sqlalchemy.

Track the number of queries we do, if we exceed a configurable amount emit a stack trace.
Track the time spent doing queries, if any single query takes too long, or the request is taking too long
  emit a stack trace.
Rate limit these traces to avoid flooding the logs.
"""
from sqlalchemy import event
import time
import logging
import traceback
from functools import partial

try:
    from flask import _app_ctx_stack as stack
except ImportError:
    from flask import _request_ctx_stack as stack

from flask import request

log = logging.getLogger(__name__)


class FlaskSqlaDebugException(Exception):
    """Generic exception for now.  Might attach more detailed information later."""

    pass


class SqlDebugWith(object):
    """Allow a python 'with' statement to output sql.

    example usage:
        with SqlDebugWith(app.FlaskSqlDebugApp):
            # do some ORM stuff here
    """

    def __init__(self, debug_obj):
        """Pass in a FlaskSqlaDebug object here."""
        self.obj = debug_obj

    def __enter__(self):
        """Typical __enter__ signature."""
        self.obj.query_dump_start()
        return self

    def __exit__(self, type, value, traceback):
        """Typical __exit__ signature."""
        self.obj.query_dump_stop()


class FlaskSqlaDebug(object):
    """Attach one of these to your flask app to get debugging and tracing."""

    def __init__(self, *args, **kwargs):
        """Track the number of stack dumps done to avoid blowing up the logging system."""
        self.last_stack_dump = round(time.time())
        self.global_stack_dumps = 0

        # for more fine grained mock patching for unit tests, this lets us simulate longer running queries.
        self.before_cursor_execute_time = time.time
        self.after_cursor_execute_time = time.time

        if "app" not in kwargs:
            raise ValueError("missing 'app' kwargs, needs to be your flask app")
        self.app = kwargs["app"]

        if "engine" not in kwargs:
            raise ValueError("missing 'engine' in kwargs, needs to be your sqlalchemy engine")
        self.engine = kwargs["engine"]

        if "config" in kwargs:
            self.config = kwargs["config"]
        else:
            self.config = self.app.config

        if "logger" in kwargs:
            self.log = kwargs["logger"]
        else:
            self.log = log

        self.app.before_request(self._before_request_handler)
        self.app.after_request(self._after_request_handler)

        """Add our debug sql hooks for printing and timing"""
        event.listen(self.engine, 'before_cursor_execute', self._before_cursor_execute)
        event.listen(self.engine, 'after_cursor_execute', self._after_cursor_execute)

        self._g_name = 'FlaskSqlDebugApp-' + str(id(self))

    def _set_g_val(self, value, gname):
        """Accessor setter method for 'g' (per request) vars, see _make_g_accessor()."""
        # log.info("value: {}, gname: {}".format(value,gname))
        self._get_g()[gname] = value

    def _get_g_val(self, gname):
        """Accessor getter method for 'g' (per request) vars, see _make_g_accessor()."""
        return self._get_g()[gname]

    @classmethod
    def _make_g_accessor(cls, gname, doc):
        """Make an accessor for a 'g' (per-request) variable.

        Example:
            FlaskSqlaDebug._make_g_accessor("sql_max_query_count", "Max sql queries per request")

        This will make an accessor on the object sql_max_query_count that maps to the per-request 'sql_max_query_count' variable.
        See the bottom of this file for the accessors made.
        """
        setattr(cls, gname, property(partial(cls._get_g_val, gname=gname), partial(cls._set_g_val, gname=gname), None, doc))

    def _get_g(self):
        ctx = stack.top
        if ctx is None:
            return None
        g = getattr(ctx, self._g_name, None)
        if g is None:
            g = self._default_data()
        setattr(ctx, self._g_name, g)
        return g

    def _default_data(self, g=None):
        if g is None:
            g = dict()
        config = self.config
        """
        Track how long this request has taken so we can alert if it takes long
        """
        g["start_time"] = time.time()
        g["throw_exception"] = config.get("FLASK_SQLA_DEBUG_THROW_EXCEPTION", False)
        """
        Track the count of queries so we know if an endpoint is making many
        sql queries.
        """
        g["sql_query_count"] = 0
        g["sql_max_query_count"] = config.get("FLASK_SQLA_DEBUG_MAX_QUERY_COUNT", 10)
        g["sql_max_single_query_seconds"] = config.get("FLASK_SQLA_DEBUG_MAX_SINGLE_QUERY_SECONDS", 0.2)
        g["sql_max_total_query_seconds"] = config.get("FLASK_SQLA_DEBUG_MAX_TOTAL_QUERY_SECONDS", 0.4)
        g["sql_total_query_time"] = 0
        g["sql_total_query_time_exceeded"] = False

        """Track if we are dumping queries or not, this is nestable."""
        g["dump_queries"] = 0
        """
        Track the number of stack traces we dumped due to problems to
        prevent runaway logging
        """
        g["stack_dump_count"] = 0
        g["stack_dump_request_count"] = 0
        return g

    def _before_request_handler(self):
        """Set data used to track number of queries and debug time taken for requests."""
        self._default_data(self._get_g())

    def _after_request_handler(self, response):
        g = self._get_g()
        total_time = time.time() - g["start_time"]

        exceeded_str = ""
        if g["sql_query_count"] >= g["sql_max_query_count"]:
            exceeded_str = " (exceeded max)"

        self.log.debug(
            "Total time: %0.3f, query_count: %d %s, stacks_dumped: %d, stacks_requested %d",
            total_time, g["sql_query_count"], exceeded_str, g["stack_dump_count"], g["stack_dump_request_count"]
        )
        return response

    def maybe_dump_stack(self, fmt, *args):
        """Dump the stack, but rate limit so as not to blow out the logs.

        This should be called when we detect something bad happening,
        a slow query, or too many queries or otherwise a strange condition.
        """
        g = self._get_g()
        config = self.config
        if g is not None:
            g["stack_dump_request_count"] += 1
            if g["stack_dump_count"] > config.get('FLASK_SQLA_DEBUG_MAX_REQUEST_DEBUG_STACKS', 3):
                return

        # limit to MAX_GLOBAL_DEBUG_STACKS per second.
        curtime = round(time.time())
        if curtime == self.last_stack_dump and \
                self.global_stack_dumps > config.get("FLASK_SQLA_DEBUG_MAX_GLOBAL_DEBUG_STACKS", 20):
            return
        self.last_stack_dump = curtime
        self.global_stack_dumps += 1

        g["stack_dump_count"] += 1

        if self.throw_exception:
            s = "url: " + request.url + "\n"
            s = fmt % tuple(args)
            raise FlaskSqlaDebugException(s)
        a = ["url: " + request.url + "\n"]
        a.extend(args)
        a.append("".join(traceback.format_stack()))
        self.log.error("%s\n" + fmt + ": stack trace: %s", *a)

    def query_dump_start(self):
        """Start logging queries."""
        self._get_g()["dump_queries"] += 1

    def query_dump_stop(self):
        """Stop logging queries."""
        g = self._get_g()
        g["dump_queries"] -= 1
        if g["dump_queries"] < 0:
            self.maybe_dump_stack("query_dump_stop called unbalanced with query_dump_start")

    def _before_cursor_execute(self, conn, cursor, statement,
                               parameters, context, executemany):
        g = self._get_g()
        if g is None:
            return

        g["query_start_time"] = self.before_cursor_execute_time()
        # If not dumping queries, we are done here.
        if g["dump_queries"] > 0:
            self.log.debug("Executing query: %s, params: %s", statement, parameters)

    def _after_cursor_execute(self, conn, cursor, statement,
                              parameters, context, executemany):
        """
        Do accounting post statement.

        After each sql statement track the following things:

        1) Did we exceed the total amount of time spent waiting on the db?
        2) Did we exceed the max time for a single query?
        3) Did we exceed the max number of queries for this call?
        """
        g = self._get_g()
        if g is None:
            return

        time_taken = self.after_cursor_execute_time() - g["query_start_time"]

        # log.debug("Total time: %0.3f, query_count: %d, stacks_dumped: %d", time_taken, g["sql_query_count"], g["stack_dump_count"])

        g["sql_query_count"] += 1

        logged = False
        # Make sure we haven't exceeded the TOTAL time for all sql
        g["sql_total_query_time"] += time_taken
        if g["sql_total_query_time"] > g["sql_max_total_query_seconds"]:
            if not g["sql_total_query_time_exceeded"]:
                g["sql_total_query_time_exceeded"] = True
                self.maybe_dump_stack("Total query time exceeded for multiple queries, last query: %s, params %s", statement, parameters)
                logged = True

        # Make sure we haven't exceeded the time for a SINGLE sql
        if not logged and g["sql_max_single_query_seconds"] < time_taken:
            self.maybe_dump_stack("Total query time exceeded for multiple queries, last query: %s, params %s", statement, parameters)
            logged = True

        # Did we exceed the max queries we should be doing?
        if not logged and g["sql_query_count"] == g["sql_max_query_count"]:
            self.maybe_dump_stack("Max queries per request exceeded, last query: %s, params: %s", statement, parameters)
            logged = True

        if g["dump_queries"] > 0:
            self.log.debug("Query finished in {} seconds.".format(time_taken))


FlaskSqlaDebug._make_g_accessor("sql_max_query_count", "Max sql queries per request")
FlaskSqlaDebug._make_g_accessor("sql_max_single_query_seconds", "Max seconds for a single query")
FlaskSqlaDebug._make_g_accessor("sql_max_total_query_seconds", "Max seconds total for sql queries")
FlaskSqlaDebug._make_g_accessor("throw_exception", "Throw an actual exception instead of just logging")
