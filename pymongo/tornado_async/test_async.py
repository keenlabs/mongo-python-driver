# Copyright 2011-2012 10gen, Inc.
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

"""Test the Tornado asynchronous Python driver for MongoDB."""

import unittest
import sys
import time

import pymongo
import pymongo.objectid
from pymongo.tornado_async import async
import tornado.ioloop

# Tornado testing tools
from pymongo.tornado_async import eventually, puritanical

def delay(ms):
    """
    Create a delaying $where clause. Note that you can only have one of these
    Javascript functions running on the server at a time, see SERVER-4258.
    @param ms:  Time to delay, in milliseconds
    @return:    A Javascript $where clause that delays for that time
    """
    return """
        function() {
            var d = new Date((new Date()).getTime() + %d);
            while (d > (new Date())) { };
            return true;
        }
    """ % ms


class AsyncTest(
    puritanical.PuritanicalTest,
    eventually.AssertEventuallyTest
):
    def setUp(self):
        super(AsyncTest, self).setUp()
        self.sync_cx = pymongo.Connection()
        self.sync_db = self.sync_cx.test
        self.sync_coll = self.sync_db.test_collection
        self.sync_coll.remove()
        self.sync_coll.ensure_index([('s', pymongo.ASCENDING)], unique=True)
        self.sync_coll.insert(
            [{'_id': i, 's': hex(i)} for i in range(200)],
            safe=True
        )

    def test_repr(self):
        cx = async.AsyncConnection()
        self.assert_(repr(cx).startswith('AsyncConnection'))
        db = cx.test
        self.assert_(repr(db).startswith('AsyncDatabase'))
        coll = db.test
        self.assert_(repr(coll).startswith('AsyncCollection'))

    def test_cursor(self):
        """
        Test that we get a regular Cursor if we don't pass a callback to find(),
        and we get an AsyncCursor if we do pass a callback.
        """
        coll = async.AsyncConnection().test.foo
        # We're not actually running the find(), so null callback is ok
        cursor = coll.find(callback=lambda: None)
        self.assert_(isinstance(cursor, async.AsyncCursor))
        cursor = coll.find()
        self.assertFalse(isinstance(cursor, async.AsyncCursor))

    def test_find(self):
        results = []
        def callback(result, error):
            self.assert_(error is None)
            results.append(result)

        async.AsyncConnection().test.test_collection.find(
            {'_id': 1},
            callback=callback
        )

        def foo(*args, **kwargs):
            return results and results[0]

        self.assertEventuallyEqual(
            [{'_id': 1, 's': hex(1)}],
            foo
        )

        tornado.ioloop.IOLoop.instance().start()

    def test_find_default_batch(self):
        results = []
        cursor = None

        def callback(result, error):
            self.assert_(error is None)
            results.append(result)
            if cursor.alive:
                cursor.get_more(callback=callback)

        cursor = async.AsyncConnection().test.test_collection.find(
            {},
            {'s': 0}, # Don't return the 's' field
            sort=[('_id', pymongo.ASCENDING)],
            callback=callback
        )

        # You know what's weird? MongoDB's default first batch is weird. It's
        # 101 records or 1MB, whichever comes first.
        self.assertEventuallyEqual(
            [{'_id': i} for i in range(101)],
            lambda: results and results[0]
        )

        # Next batch has remainder of 1000 docs
        self.assertEventuallyEqual(
            [{'_id': i} for i in range(101, 200)],
            lambda: len(results) >= 2 and results[1]
        )

        tornado.ioloop.IOLoop.instance().start()

    def test_batch_size(self):
        batch_size = 3
        limit = 15
        results = []
        cursor = None

        def callback(result, error):
            self.assert_(error is None)
            results.append(result)
            if cursor.alive:
                cursor.get_more(callback=callback)

        cursor = async.AsyncConnection().test.test_collection.find(
            {},
            {'s': 0}, # Don't return the 's' field
            sort=[('_id', pymongo.ASCENDING)],
            callback=callback,
            batch_size=batch_size,
            limit=limit,
        )

        expected_results = [
            [{'_id': i} for i in range(start_batch, start_batch + batch_size)]
            for start_batch in range(0, limit, batch_size)
        ]

        self.assertEventuallyEqual(
            expected_results,
            lambda: results
        )

        tornado.ioloop.IOLoop.instance().start()

    def test_find_is_async(self):
        """
        Confirm find() is async by launching three operations which will finish
        out of order.
        """
        # Make a big unindexed collection that will take a long time to query
        self.sync_db.drop_collection('big_coll')
        self.sync_db.big_coll.insert([
            {'s': hex(s)} for s in range(10000)
        ])

        results = []

        def callback(result, error):
            #print >> sys.stderr, 'result',result
            self.assert_(error is None)
            results.append(result)

        # Launch 3 find operations for _id's 1, 2, and 3, which will finish in
        # order 2, 3, then 1.
        loop = tornado.ioloop.IOLoop.instance()

        # This find() takes 1 second
        loop.add_timeout(
            time.time() + 0.1,
            lambda: async.AsyncConnection().test.test_collection.find(
                {'_id': 1, '$where': delay(1000)},
                fields={'s': True, '_id': False},
                callback=callback
            )
        )

        # Very fast lookup
        loop.add_timeout(
            time.time() + 0.2,
            lambda: async.AsyncConnection().test.test_collection.find(
                {'_id': 2},
                fields={'s': True, '_id': False},
                callback=callback
            )
        )

        # Find {'i': 3} in big_coll -- even though there's only one such record,
        # MongoDB will have to scan the whole table to know that. We expect this
        # to be faster than 1 second (the $where clause above) and slower than
        # the indexed lookup below.
        loop.add_timeout(
            time.time() + 0.3,
            lambda: async.AsyncConnection().test.big_coll.find(
                {'s': hex(3)},
                fields={'s': True, '_id': False},
                callback=callback
            )
        )

        # Results were appended in order 2, 3, 1
        self.assertEventuallyEqual(
            [[{'s': hex(s)}] for s in (2, 3, 1)],
            lambda: results
        )

        tornado.ioloop.IOLoop.instance().start()

    def test_find_one(self):
        """
        Confirm find_one() is async by launching two operations which will
        finish out of order.
        """
        results = []

        def callback(result, error):
            # print >> sys.stderr, 'result',result
            self.assert_(error is None)
            results.append(result)

        # Launch 2 find_one operations for _id's 1 and 2, which will finish in
        # order 2 then 1.
        loop = tornado.ioloop.IOLoop.instance()

        # This find_one() takes half a second
        loop.add_timeout(
            time.time() + 0.1,
            lambda: async.AsyncConnection().test.test_collection.find(
                {'_id': 1, '$where': delay(500)},
                fields={'s': True, '_id': False},
                callback=callback
            )
        )

        # Very fast lookup
        loop.add_timeout(
            time.time() + 0.2,
            lambda: async.AsyncConnection().test.test_collection.find(
                {'_id': 2},
                fields={'s': True, '_id': False},
                callback=callback
            )
        )

        # Results were appended in order 2, 1
        self.assertEventuallyEqual(
            [[{'s': hex(s)}] for s in (2, 1)],
            lambda: results
        )

        tornado.ioloop.IOLoop.instance().start()

    def test_update(self):
        results = []

        def callback(result, error):
            self.assert_(error is None)
            results.append(result)

        async.AsyncConnection().test.test_collection.update(
            {'_id': 5},
            {'$set': {'foo': 'bar'}},
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))
        self.assertEventuallyEqual(1, lambda: results[0]['ok'])
        self.assertEventuallyEqual(True, lambda: results[0]['updatedExisting'])
        self.assertEventuallyEqual(1, lambda: results[0]['n'])
        self.assertEventuallyEqual(None, lambda: results[0]['err'])

        tornado.ioloop.IOLoop.instance().start()

    def test_update_bad(self):
        """
        Violate a unique index, make sure we handle error well
        """
        results = []

        def callback(result, error):
            self.assert_(isinstance(error, pymongo.errors.DuplicateKeyError))
            self.assertEqual(None, result)
            results.append(result)

        async.AsyncConnection().test.test_collection.update(
            {'_id': 5},
            {'$set': {'s': hex(4)}}, # There's already a document with s: hex(4)
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))

        tornado.ioloop.IOLoop.instance().start()
            
    def test_insert(self):
        results = []

        def callback(result, error):
            # print >> sys.stderr, 'result', result
            # print >> sys.stderr, 'error', error
            self.assert_(error is None)
            results.append(result)

        async.AsyncConnection().test.test_collection.insert(
            {'_id': 201},
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))

        # insert() returns new _id
        self.assertEventuallyEqual(201, lambda: results[0])

        tornado.ioloop.IOLoop.instance().start()

    def test_insert_many(self):
        results = []

        def callback(result, error):
            # print >> sys.stderr, 'result', result
            # print >> sys.stderr, 'error', error
            self.assert_(error is None)
            results.append(result)

        async.AsyncConnection().test.test_collection.insert(
            [{'_id': i, 's': hex(i)} for i in range(201, 211)],
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))
        self.assertEventuallyEqual(range(201, 211), lambda: results[0])

        tornado.ioloop.IOLoop.instance().start()

    def test_insert_bad(self):
        """
        Violate a unique index, make sure we handle error well
        """
        results = []

        def callback(result, error):
            print >> sys.stderr, 'result', result
            print >> sys.stderr, 'error', error
            self.assert_(isinstance(error, pymongo.errors.DuplicateKeyError))
            self.assertEqual(None, result)
            results.append(result)

        async.AsyncConnection().test.test_collection.insert(
            {'s': hex(4)}, # There's already a document with s: hex(4)
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))

        tornado.ioloop.IOLoop.instance().start()
        
    def test_insert_many_one_bad(self):
        """
        Violate a unique index in one of many updates, make sure we handle error
        well
        """
        results = []

        def callback(result, error):
            # print >> sys.stderr, 'result', result
            # print >> sys.stderr, 'error', error
            self.assert_(isinstance(error, pymongo.errors.DuplicateKeyError))
            results.append(result)

        async.AsyncConnection().test.test_collection.insert(
            [
                {'_id': 201, 's': hex(201)},
                {'_id': 202, 's': hex(4)}, # Already exists
                {'_id': 203, 's': hex(203)},
            ],
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))
        self.assertEventuallyEqual(None, lambda: results[0])

        tornado.ioloop.IOLoop.instance().start()

        # First insert should've succeeded
        self.assertEqual(
            [{'_id': 201, 's': hex(201)}],
            list(self.sync_db.test_collection.find({'_id': 201}))
        )

        # Final insert didn't execute, since second failed
        self.assertEqual(
            [],
            list(self.sync_db.test_collection.find({'_id': 203}))
        )

    def test_save_with_id(self):
        results = []

        def callback(result, error):
            # print >> sys.stderr, 'result', result
            self.assert_(error is None)
            results.append(result)

        async.AsyncConnection().test.test_collection.save(
            {'_id': 5},
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))

        # save() returns the _id, in this case 5
        self.assertEventuallyEqual(5, lambda: results[0])

        tornado.ioloop.IOLoop.instance().start()

    def test_save_without_id(self):
        results = []

        def callback(result, error):
            print >> sys.stderr, 'result', result
            print >> sys.stderr, 'error', error
            self.assert_(error is None)
            results.append(result)

        async.AsyncConnection().test.test_collection.save(
            {'fiddle': 'faddle'},
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))

        # save() returns the new _id
        self.assertEventuallyEqual(
            True,
            lambda: isinstance(results[0], pymongo.objectid.ObjectId)
        )

        tornado.ioloop.IOLoop.instance().start()

    def test_save_bad(self):
        """
        Violate a unique index, make sure we handle error well
        """
        results = []

        def callback(result, error):
            self.assert_(isinstance(error, pymongo.errors.DuplicateKeyError))
            self.assertEqual(None, result)
            results.append(result)

        async.AsyncConnection().test.test_collection.save(
            {'_id': 5},
            {'$set': {'s': hex(4)}}, # There's already a document with s: hex(4)
            callback=callback,
        )

        self.assertEventuallyEqual(1, lambda: len(results))

        tornado.ioloop.IOLoop.instance().start()




# TODO: test insert, save, remove
# TODO: test that unsafe operations don't call the callback
# TODO: replicate asyncmongo's whole test suite?
# TODO: apply pymongo's whole suite to async

if __name__ == '__main__':
    unittest.main()
