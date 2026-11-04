import time

USERS = {i: f"user{i}" for i in range(50)}
RATES = {"USD": 1.0, "EUR": 0.9}
TIMINGS = []


def get_user(user_id):
    time.sleep(0.002)
    return USERS[user_id]


def get_rate(currency):
    """Same answer every time, but it reads the clock to log its own speed."""
    started = time.time()
    time.sleep(0.002)
    rate = RATES[currency]
    TIMINGS.append(time.time() - started)
    return rate


def stamp(label):
    return f"{label}:{time.time()}"


def parse_flag(text):
    return text.strip().lower() == "yes"

def total_price(items):
    time.sleep(0.002)
    return sum(items)

def main():
    for i in range(300):
        get_user(i % 20)
    for _ in range(40):
        total_price([1, 2, 3])
    for i in range(100):
        get_rate(["USD", "EUR"][i % 2])
    for _ in range(50):
        stamp("tick")
    for _ in range(500):
        parse_flag(" YES ")


if __name__ == "__main__":
    main()