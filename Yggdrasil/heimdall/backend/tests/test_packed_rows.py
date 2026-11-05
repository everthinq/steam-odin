import math
import pickle

from packed_rows import BOOL, FLOAT, INT, PackedEntries, PackedRows


def test_values_read_back_exactly_including_missing_and_unpackable_ones():
    rows = PackedRows((FLOAT, INT, BOOL, FLOAT))
    rows.set('a', (1.5, 3, True, None))
    rows.set('b', (None, None, None, {'overstock': 1}))
    rows.set('c', (math.nan, 2 ** 70, False, 2.0))
    assert rows.get('a') == (1.5, 3, True, None)
    assert rows.get('b') == (None, None, None, {'overstock': 1})
    nan, big, flag, value = rows.get('c')
    assert math.isnan(nan) and big == 2 ** 70 and flag is False and value == 2.0
    assert rows.get('missing') is None and list(rows) == ['a', 'b', 'c'] and 'b' in rows and len(rows) == 3


def test_replacing_a_row_keeps_its_place_and_forgets_old_unpackable_values():
    rows = PackedRows((FLOAT, INT))
    rows.set('a', ({'odd': True}, 1))
    rows.set('b', (2.0, 2))
    rows.set('a', (5, 7))
    assert list(rows.items()) == [('a', (5.0, 7)), ('b', (2.0, 2))]


def test_price_table_entries_keep_the_tuple_shape_and_survive_pickling():
    quote = (1.51, 1, False, True, None, 1.2, 1793852666)
    entries = PackedEntries({'Case': (quote, None, 3.5, None), 'Key': (None, quote, None, 10.0)})
    again = pickle.loads(pickle.dumps(entries))
    assert again == {'Case': (quote, None, 3.5, None), 'Key': (None, quote, None, 10.0)}
    assert again.get('nothing') is None and list(again) == ['Case', 'Key']
