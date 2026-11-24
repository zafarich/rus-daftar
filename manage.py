"""Mavzularni boshqarish.

  python manage.py import topics/01_predlozhny.json [--activate]
  python manage.py check  topics/02_xxx.json      # faqat tekshirish, bazaga yozmaydi
  python manage.py list
  python manage.py activate <slug> [--restart]
"""
import json
import sys

from app import db


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    cmd, args = argv[1], argv[2:]
    try:
        if cmd == "import":
            r = db.import_topic(load(args[0]), activate="--activate" in args)
            print(f"✔ {r['slug']}: {r['items']} ta savol yozildi, {r['removed']} ta eskisi o'chirildi")
        elif cmd == "check":
            items = db._items_from(load(args[0]))
            kinds = {}
            for it in items:
                kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
            print("✔ Fayl to'g'ri:", ", ".join(f"{k}={v}" for k, v in kinds.items()))
        elif cmd == "list":
            for t in db.list_topics():
                print(f"{'*' if t['active'] else ' '} {t['slug']:<24} {t['n_items']:>4} savol  "
                      f"boshlangan: {t['started_on'] or '-'}  {t['title']}")
        elif cmd == "activate":
            t = db.activate_topic(args[0], restart="--restart" in args)
            print(f"✔ Faol: {t['title']} (boshlangan {t['started_on']})")
        else:
            print(__doc__)
            return 1
    except (db.TopicError, FileNotFoundError, json.JSONDecodeError, KeyError, IndexError) as e:
        print("✘", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
