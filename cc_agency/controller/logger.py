import os
from threading import Thread
from queue import Queue


class Logger:
    def __init__(self, conf):
        self._log_file_path = os.path.expanduser(conf.d['controller']['log_file_path'])

        log_file_dir, log_file_name = os.path.split(self._log_file_path)
        if not os.path.exists(log_file_dir):
            os.makedirs(log_file_dir)

        self._q = Queue()
        t = Thread(target=self._pull)
        t.start()

    def push(self, data):
        self._q.put(data)

    def _pull(self):
        log_file_path = self._log_file_path

        while True:
            data = self._q.get()
            with open(log_file_path, 'w') as f:
                for line in data['lines']:
                    print(line, file=f)
