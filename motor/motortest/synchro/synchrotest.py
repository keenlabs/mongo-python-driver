# Copyright 2012 10gen, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Test Motor by testing that Synchro, a fake PyMongo implementation built on
top of Motor, passes the same unittests as PyMongo.

This program monkey-patches sys.modules, so run it alone, rather than as part
of a larger test suite.

The environment variable TIMEOUT_SEC controls how long Synchro waits for each
Motor operation to complete, default 5 seconds.
"""

import sys
sys.path[0:0] = [""]
from os import path

import nose
from nose.config import Config
from nose.plugins import Plugin
from nose.plugins.manager import PluginManager
from nose.selector import Selector

from motor.motortest import synchro
from motor.motortest.puritanical import PuritanicalIOLoop

excluded_modules = [
    'test.test_threads',
    'test.test_threads_replica_set_connection',
    'test.test_pooling',
    'test.test_pooling_gevent',
    'test.test_paired',

    # TODO:
    'test.test_ssl',
    'test.test_master_slave_connection',
    'test.test_grid_file',
    'test.test_gridfs',
]

# TODO: document these variations and omissions b/w PyMongo and the Motor API
excluded_tests = [
    # Synchro can't simulate requests, so test copy_db in Motor directly.
    'TestConnection.test_copy_db',

    # Tested directly against Motor
    'TestMasterSlaveConnection.test_disconnect',

    # These tests require a lot of PyMongo-specific monkey-patching, we're
    # not going to test them in Motor because master-slave uses the same logic
    # under the hood and can be assumed to work.
    'TestMasterSlaveConnection.test_raise_autoreconnect_if_all_slaves_fail',
    'TestMasterSlaveConnection.test_continue_until_slave_works',

    # Motor's reprs aren't the same as PyMongo's
    '*.test_repr',

    # Motor doesn't do requests
    'TestConnection.test_auto_start_request',
    'TestConnection.test_contextlib_auto_start_request',
    'TestConnection.test_with_start_request',
    'TestMasterSlaveConnection.test_insert_find_one_in_request',
    'TestDatabase.test_authenticate_and_request',
    'TestGridfs.test_request',

    # test_replica_set_connection: We test this directly, because it requires
    # monkey-patching either socket or IOStream, depending on whether it's
    # PyMongo or Motor
    'TestConnection.test_auto_reconnect_exception_when_read_preference_is_secondary',

    # Motor doesn't support forking or threading
    'TestConnection.test_fork',
    'TestConnection.test_interrupt_signal',
    'TestCollection.test_ensure_unique_index_threaded',
    'TestGridfs.test_threaded_writes',
    'TestGridfs.test_threaded_reads',

    # Motor doesn't support PyMongo's syntax, db.system.js['my_func'] = "code",
    # users should just use system.js as a regular collection
    'TestDatabase.test_system_js',
    'TestDatabase.test_system_js_list',

    # Motor can't raise an index error if a cursor slice is out of range; it
    # just gets no results
    'TestCursor.test_getitem_index_out_of_range',

    # Motor's tailing works differently
    'TestCursor.test_tailable',
]


class SynchroNosePlugin(Plugin):
    name = 'synchro'

    def __init__(self, *args, **kwargs):
        # We need a standard Nose selector in order to filter out methods that
        # don't match TestSuite.test_*
        self.selector = Selector(config=None)
        super(SynchroNosePlugin, self).__init__(*args, **kwargs)

    def configure(self, options, conf):
        super(SynchroNosePlugin, self).configure(options, conf)
        self.enabled = True

    def wantModule(self, module):
        return module.__name__ not in excluded_modules

    def wantMethod(self, method):
        # Run standard Nose checks on name, like "does it start with test_"?
        if not self.selector.matches(method.__name__):
            return False

        for excluded_name in excluded_tests:
            suite_name, method_name = excluded_name.split('.')
            if ((method.im_class.__name__ == suite_name or suite_name == '*')
                and method.__name__ == method_name
            ):
                return False

        return True


if __name__ == '__main__':
    PuritanicalIOLoop().install()

    # Monkey-patch all pymongo's unittests so they think Synchro is the
    # real PyMongo
    sys.modules['pymongo'] = synchro

    for submod in [
        'connection',
        'collection',
        'master_slave_connection',
        'replica_set_connection',
        'database',
        'pool',
    ]:
        # So that e.g. 'from pymongo.connection import Connection' gets the
        # Synchro Connection, not the real one.
        sys.modules['pymongo.%s' % submod] = synchro

    # Find our directory
    this_dir = path.dirname(__file__)

    # Find test dir
    test_dir = path.normpath(path.join(this_dir, '../../../test'))
    print 'Running tests in %s' % test_dir

    config = Config(
        plugins=PluginManager(),
    )

    nose.main(
        config=config,
        addplugins=[SynchroNosePlugin()],
        defaultTest=test_dir,
    )
