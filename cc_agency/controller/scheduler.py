from threading import Thread
from queue import Queue

from cc_agency.controller.docker import ClientProxy


class Scheduler:
    def __init__(self, conf, mongo):
        self._conf = conf
        self._mongo = mongo

        mongo.db['nodes'].drop()

        self._scheduling_q = Queue(maxsize=1)
        self._nodes = {
            node_name: ClientProxy(node_name, conf, mongo)
            for node_name
            in conf.d['controller']['docker']['nodes'].keys()
        }

        Thread(target=self._scheduling_loop).start()
        self.schedule()

    def schedule(self):
        try:
            self._scheduling_q.put_nowait(None)
        except:
            pass

    def _scheduling_loop(self):
        while True:
            self._scheduling_q.get()
            print('start scheduling')

            # clean broken batches

            # offline node inspection
            for node in self._nodes:
                node.inspect_offline_node_async()

            # schedule

            print('end scheduling')
