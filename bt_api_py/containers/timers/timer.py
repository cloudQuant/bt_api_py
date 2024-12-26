class TimerData(object):
    def __init__(self, data):
        self.event_type = "Timer_update"
        self.data = data

    def get_data(self):
        return self.data

    def __str__(self):
        return str(self.data)
