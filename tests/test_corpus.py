from datetime import date, timedelta

from copom_mcp.corpus import Meeting, attach_decisions


def daily(start: str, values: list[float]) -> dict[str, float]:
    day = date.fromisoformat(start)
    return {(day + timedelta(days=i)).isoformat(): v for i, v in enumerate(values)}


def test_decision_waits_for_holidays():
    # 127ª: reunião em 6/6/2007, Corpus Christi no dia 7, nova meta só no dia 8.
    selic = daily("2007-06-06", [12.5, 12.5, 12.0, 12.0])
    [meeting] = attach_decisions([Meeting(127, "2007-06-06", "2007-06-14", "127ª")], selic)
    assert (meeting.decision, meeting.selic_after, meeting.change_bp) == ("corte", 12.0, -50)


def test_change_by_bias_days_later_is_not_the_meetings_decision():
    # 45ª: manteve 19% com viés de redução; o corte para 18,5% entrou 7 dias depois.
    selic = daily("2000-03-22", [19.0] * 7 + [18.5])
    [meeting] = attach_decisions([Meeting(45, "2000-03-22", "2000-03-30", "45ª")], selic)
    assert (meeting.decision, meeting.selic_after) == ("manutenção", 19.0)


def test_maintenance_and_meetings_before_the_target():
    selic = daily("2026-01-28", [15.0] * 10)
    meetings = [Meeting(276, "2026-01-28", "2026-02-03", "276ª"), Meeting(30, "1998-12-16", "1998-12-20", "30ª")]
    held, old = attach_decisions(meetings, selic)
    assert (held.decision, held.selic_after) == ("manutenção", 15.0)
    assert old.decision is None
