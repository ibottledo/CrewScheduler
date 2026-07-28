
def validate_schedule(config, solution):
    """
    생성된 스케줄이 하드 제약 조건을 만족하는지 검증합니다.
    위반 목록을 반환합니다. 빈 목록은 스케줄이 유효함을 의미합니다.
    """
    if not solution:
        return ["검증할 솔루션이 없습니다."]

    violations = []
    
    num_days = config['num_days']
    num_employees = config['num_employees']
    all_employees = list(range(num_employees))
    all_days = list(range(num_days))
    
    group_a = config['groups']['a']
    group_b = config['groups']['b']
    shifts = list(config['shifts'].keys())
    vacations = [tuple(v) for v in config['vacations']]

    # 휴가 제약 조건 확인
    for emp, day in vacations:
        if 0 <= day < num_days and solution.get(emp, {}).get(day) != 'off':
            # 휴가일이지만 직원이 'off'가 아님
            # 참고: 솔버는 0을 할당하도록 제약되지만, 최종 출력을 확인합니다.
            # 스케줄 출력에서는 '휴가'로 표시될 수 있지만, 내부 값은 'off'여야 합니다.
            is_working = any(solution.get(emp, {}).get(day) == s for s in shifts)
            if is_working:
                violations.append(f"검증 오류: 직원 {emp}가 휴가일인 {day}일에 근무 중입니다.")

    # 일일 근무조 충족 여부 확인
    for d in all_days:
        d_coverage = {s: 0 for s in shifts}
        d_coverage_group_a = {s: 0 for s in shifts}
        d_coverage_group_b = {s: 0 for s in shifts}
        
        for e in all_employees:
            shift = solution[e][d]
            if shift != 'off':
                d_coverage[shift] += 1
                if e in group_a:
                    d_coverage_group_a[shift] += 1
                if e in group_b:
                    d_coverage_group_b[shift] += 1

        if d_coverage_group_a.get('D', 0) != 1:
            violations.append(f"검증 오류: {d+1}일, A그룹 'D' 근무 인원 {d_coverage_group_a.get('D', 0)}명, 필요 인원 1명.")
        if d_coverage_group_b.get('D', 0) != 1:
            violations.append(f"검증 오류: {d+1}일, B그룹 'D' 근무 인원 {d_coverage_group_b.get('D', 0)}명, 필요 인원 1명.")
        if d_coverage_group_a.get('N', 0) != 1:
            violations.append(f"검증 오류: {d+1}일, A그룹 'N' 근무 인원 {d_coverage_group_a.get('N', 0)}명, 필요 인원 1명.")
        if d_coverage_group_b.get('N', 0) != 1:
            violations.append(f"검증 오류: {d+1}일, B그룹 'N' 근무 인원 {d_coverage_group_b.get('N', 0)}명, 필요 인원 1명.")
        if d_coverage.get('E', 0) != 1:
            violations.append(f"검증 오류: {d+1}일, 'E' 근무 인원 {d_coverage.get('E', 0)}명, 필요 인원 1명.")

    # 직원별 제약 조건 확인
    for e in all_employees:
        # 금지된 연속 근무 (E->D, D->N)
        for d in range(num_days - 1):
            if solution[e][d] == 'E' and solution[e][d+1] == 'D':
                violations.append(f"검증 오류: 직원 {e}가 {d+1}일에 금지된 E -> D 연속 근무를 합니다.")
            if solution[e][d] == 'D' and solution[e][d+1] == 'N':
                violations.append(f"검증 오류: 직원 {e}가 {d+1}일에 금지된 D -> N 연속 근무를 합니다.")

        # 최대 3일 연속 휴무 (즉, 4일 연속 휴무 불가)
        for d in range(num_days - 3):
            # 휴가가 아니고 근무가 'off'인 경우 '휴무'로 간주
            is_off_day = lambda day: solution[e][day] == 'off' and (e, day) not in vacations
            if all(is_off_day(d+i) for i in range(4)):
                 violations.append(f"검증 오류: 직원 {e}가 {d+1}일부터 4일 이상 연속으로 휴무합니다.")

        # 최대 5일 연속 근무
        for d in range(num_days - 5):
            is_working = lambda day: solution[e][day] != 'off' and (e, day) not in vacations
            if sum(is_working(d+i) for i in range(6)) > 5:
                violations.append(f"검증 오류: 직원 {e}가 {d+1}일부터 5일 이상 연속으로 근무합니다.")

        # 최대 3일 연속 동일 근무
        for s in shifts:
            for d in range(num_days - 3):
                if all(solution[e][d+i] == s for i in range(4)):
                    violations.append(f"검증 오류: 직원 {e}가 {d+1}일부터 3일 이상 연속으로 '{s}' 근무를 합니다.")

    return violations
