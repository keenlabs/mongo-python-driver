import sys
import nose
from nose.config import Config
from os import path
import re
import fake_pymongo

# TODO: running this file without a test-module name does nothing; it should run
#   all PyMongo tests except test_gevent
# TODO: test for Motor all the things test_pooling tests

if __name__ == '__main__':
    # Monkey-patch all pymongo's unittests so they think our fake pymongo is the
    # real one
    # TODO: try using 'from imp import new_module' instead of this?
    sys.modules['pymongo'] = fake_pymongo

    for submod in [
        'connection',
        'collection',
        'master_slave_connection',
        'replica_set_connection',
        'database',
    ]:
        # So that 'from pymongo.connection import Connection' gets the fake
        # Connection, not the real
        sys.modules['pymongo.%s' % submod] = fake_pymongo

    # Find our directory
    this_dir = path.dirname(__file__)

    # Find test dir
    test_dir = path.normpath(path.join(this_dir, '../../../test'))
    print 'Running tests in %s' % test_dir

    # Exclude a test that hangs and prevents the run from completing - we should
    # fix the test for async, eventually
    # TODO: fix these, or implement a Motor-specific test that exercises the
    # same features as each of these
    # TODO: document these variations and omissions b/w PyMongo and the Motor API
    # TODO: some way to specify the class or module name of these tests, not just
    #   the method name? I'm worried I'll skip more than one test if many have
    #   the same name.
    excluded_tests = [
        'test_multiprocessing',
        'test_ensure_unique_index_threaded',
        'test_interrupt_signal',
        'test_repr',
        'test_with_start_request',
        'test_fork',

        # No point supporting these in Motor
        'test_system_js',
        'test_system_js_list',
        'test_getitem_numeric_index',
        'test_getitem_slice_index',
        'test_properties',
        'test_threaded_writes',
        'test_threaded_reads',
    ]

    print 'WARNING: excluding some tests -- go in and fix them for async!'

    config = Config(
        exclude=[re.compile(et) for et in excluded_tests]
    )

    nose.run(defaultTest=test_dir, config=config)
