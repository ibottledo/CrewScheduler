import json
import os
import calendar
from scheduler import solve_monthly_crew_schedule, PENALTY_PRIORITY_MAP
from validation import validate_schedule

def calculate_total_penalty(config, solution):
    """
    주어진 스케줄의 총 페널티를 설정을 기반으로 직접 계산합니다.
    이 함수는 scheduler.py의 소프트 제약 조건 로직을 그대로 반영합니다.
    """
    total_calculated_penalty = 0
    
    num_days = config['num_days']
    num_employees = config['num_employees']
    all_employees = list(range(num_employees))
    all_days = list(range(num_days))
    
    shifts = list(config['shifts'].keys())
    shift_hours = {s: config['shifts'][s]['hours'] for s in shifts}

    crew_break_periods = {int(k): tuple(v) for k, v in config['crewX_periods'].items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_ratios = {int(k): v for k, v in config['shift_ratios'].items()}
    
    penalties_config = config['penalties']

    # 솔루션에서 특정 직원과 날짜의 근무를 가져오는 헬퍼 함수
    def get_shift(e, d):
        return solution.get(e, {}).get(d, 'off')

    def get_crew_days(e):
        break_start, break_end = crew_break_periods.get(e, (-1, -2))
        if not (0 <= break_start <= break_end < num_days):
            return []
        return [d for d in all_days if not (break_start <= d <= break_end)]

    def get_non_crew_days(e):
        break_start, break_end = crew_break_periods.get(e, (-1, -2))
        if not (0 <= break_start <= break_end < num_days):
            return []
        return [d for d in all_days if break_start <= d <= break_end]

    # --- Crew 멤버에 대한 페널티 ---
    for e in all_employees:
        crew_days = get_crew_days(e)
        is_crew_member = bool(crew_days)

        if is_crew_member:
            # 주간 평균 40시간 초과에 대한 페널티
            current_total_hours = sum(shift_hours.get(get_shift(e, d), 0) for d in crew_days if get_shift(e, d) != 'off')
            num_vacation_days = sum(1 for d in crew_days if (e, d) in vacations)
            effective_days = len(crew_days) - num_vacation_days
            
            if effective_days > 0:
                hours_over_threshold = max(0, (7 * current_total_hours) - (40 * effective_days))
                total_calculated_penalty += hours_over_threshold * PENALTY_PRIORITY_MAP[penalties_config['crew_over_40h_avg_priority']]

            # 초과 투입 페널티 (인력 부족은 이제 하드 제약 조건임)
            my_crew_days = len(crew_days)
            my_expected_hours = 0
            if my_crew_days > 0:
                vacation_days_in_crew_period = sum(1 for d in crew_days if (e, d) in vacations)
                effective_crew_days_in_period = my_crew_days - vacation_days_in_crew_period
                if effective_crew_days_in_period > 0:
                    my_expected_hours = -int(-(effective_crew_days_in_period * 40 / 7.0))
            
            current_crew_hours = sum(shift_hours.get(get_shift(e, d), 0)
                                     for d in crew_days if get_shift(e, d) != 'off')
            
            over = max(0, current_crew_hours - my_expected_hours)
            total_calculated_penalty += over * PENALTY_PRIORITY_MAP[penalties_config['over_staffing_priority']]
        
        # --- Crew 멤버가 아닌 직원에 대한 페널티 ---
        else:
            pass # 나중에 계산됨

    # --- 모든 직원에 대한 페널티 ---
    # 근무 비율 페널티
    for e in all_employees:
        w_D = sum(1 for d in all_days if get_shift(e, d) == 'D')
        w_E = sum(1 for d in all_days if get_shift(e, d) == 'E')
        w_N = sum(1 for d in all_days if get_shift(e, d) == 'N')
        total_w = w_D + w_E + w_N
        
        r_D = shift_ratios[e]['D']
        r_E = shift_ratios[e]['E']
        r_N = shift_ratios[e]['N']
        r_total = r_D + r_E + r_N
        
        if r_total > 0:
            abs_diff_D = abs(r_total * w_D - r_D * total_w)
            abs_diff_E = abs(r_total * w_E - r_E * total_w)
            abs_diff_N = abs(r_total * w_N - r_N * total_w)
            
            total_calculated_penalty += (abs_diff_D + abs_diff_E + abs_diff_N) * PENALTY_PRIORITY_MAP[penalties_config['shift_ratio_priority']]

    # 크루 기간 초과 투입의 최대값 페널티
    current_over_values = []
    for e in all_employees:
        crew_days = get_crew_days(e)
        vacation_days = sum(1 for d in crew_days if (e, d) in vacations)
        effective_crew_days = len(crew_days) - vacation_days
        expected_hours = -int(-(effective_crew_days * 40 / 7.0)) if effective_crew_days > 0 else 0
        crew_hours = sum(shift_hours.get(get_shift(e, d), 0)
                          for d in crew_days if get_shift(e, d) != 'off')
        current_over_values.append(max(0, crew_hours - expected_hours))
    
    if current_over_values:
        max_over = max(current_over_values)
        total_calculated_penalty += max_over * PENALTY_PRIORITY_MAP[penalties_config['max_over_staffing_priority']]
        
    # 비-Crew 근무의 공정성에 대한 페널티
    current_non_crew_hours = []
    for e in all_employees:
        non_crew_days = get_non_crew_days(e)
        current_non_crew_hour = sum(
            shift_hours.get(get_shift(e, d), 0)
            for d in non_crew_days
            if get_shift(e, d) != 'off'
        )
        current_non_crew_hours.append(
            current_non_crew_hour * 7 / len(non_crew_days)
            if non_crew_days else 0
        )
    
    max_nc = max(current_non_crew_hours)
    min_nc = min(current_non_crew_hours)
    total_calculated_penalty += (max_nc - min_nc) * PENALTY_PRIORITY_MAP[penalties_config['fairness_of_non_crew_work_priority']]
        
    # --- 전환 다양성 페널티 (같은 근무 연속 억제) ---
    transition_priority = PENALTY_PRIORITY_MAP.get(
        penalties_config.get('shift_transition_diversity_priority', 'medium'),
        10
    )

    for e in all_employees:
        for d in range(num_days - 1):
            cur = get_shift(e, d)
            nxt = get_shift(e, d + 1)
            # 연속한 두 날 모두 근무이고, 같은 근무 타입이면 페널티
            if cur != 'off' and nxt != 'off' and cur == nxt:
                total_calculated_penalty += transition_priority
    
    return total_calculated_penalty


def find_fixed_input_conflicts(config):
    """고정 입력만으로 즉시 판별할 수 있는 충돌을 찾습니다."""
    conflicts = []
    fixed_shifts = {
        int(employee): {int(day): shift for day, shift in shifts_by_day.items()}
        for employee, shifts_by_day in config.get('fixed_shifts', {}).items()
    }
    vacations = {tuple(vacation) for vacation in config.get('vacations', [])}
    groups = [config['groups']['a'], config['groups']['b']]
    required_by_group = [('D', groups), ('N', groups)]

    for employee, shifts_by_day in fixed_shifts.items():
        for day, shift in shifts_by_day.items():
            if (employee, day) in vacations and shift in ('D', 'E', 'N'):
                conflicts.append(f"직원 {employee}번 {day + 1}일: 휴가와 {shift} 근무가 동시에 입력되었습니다.")

        for day in range(1, config['num_days']):
            previous_shift = shifts_by_day.get(day - 1)
            current_shift = shifts_by_day.get(day)
            if previous_shift == 'E' and current_shift == 'D':
                conflicts.append(f"직원 {employee}번: {day}일과 {day + 1}일 사이 E->D 연속 근무가 금지됩니다.")
            if previous_shift == 'D' and current_shift == 'N':
                conflicts.append(f"직원 {employee}번: {day}일과 {day + 1}일 사이 D->N 연속 근무가 금지됩니다.")

        for shift in ('D', 'E', 'N'):
            for start in range(config['num_days'] - 3):
                if all(shifts_by_day.get(start + offset) == shift for offset in range(4)):
                    conflicts.append(f"직원 {employee}번: {start + 1}일부터 {start + 4}일까지 {shift} 4일 연속은 금지됩니다.")

    for day in range(config['num_days']):
        for shift, group_list in required_by_group:
            for group in group_list:
                assigned = [employee for employee in group if fixed_shifts.get(employee, {}).get(day) == shift]
                if len(assigned) > 1:
                    conflicts.append(f"{day + 1}일 {shift}: 같은 그룹에 고정 근무자가 {len(assigned)}명입니다 ({assigned}).")

        assigned_e = [employee for employee in range(config['num_employees']) if fixed_shifts.get(employee, {}).get(day) == 'E']
        if len(assigned_e) > 1:
            conflicts.append(f"{day + 1}일 E: 고정 근무자가 {len(assigned_e)}명입니다 ({assigned_e}).")

    return conflicts

def main():
    """
    Crew 스케줄링 프로세스를 실행하는 메인 함수.
    """
    # 1. 기본 설정 불러오기
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    # 2. 🌐 웹(GitHub Actions)에서 넘겨준 설정 파일(input.json) 읽기
    if os.path.exists('input.json'):
        print("--- 🌐 웹(Payload) 요청 감지: input.json 설정 업데이트 ---")
        with open('input.json', 'r', encoding='utf-8') as f:
            payload = json.load(f)
            
        # A. 연/월 적용
        if 'year' in payload and 'month' in payload:
            year = int(payload['year'])
            month = int(payload['month'])
            config['num_days'] = calendar.monthrange(year, month)[1]
            
        # B. 휴가일 적용
        if 'vacations' in payload:
            new_vacations = []
            for emp, day in payload['vacations']:
                new_vacations.append([int(emp), int(day) - 1]) # 0-indexed 변환
            config['vacations'] = new_vacations
            
        # C. 크루 휴식 기간 적용
        if 'durations' in payload:
            for emp_str, period in payload['durations'].items():
                emp_int = int(emp_str)
                start_day, end_day = period
                if start_day <= 0 or end_day <= 0:
                    config['crewX_periods'][str(emp_int)] = [-1, -2]
                else:
                    config['crewX_periods'][str(emp_int)] = [start_day - 1, end_day - 1]

        # D. 근무 비율 적용
        if 'ratios' in payload:
            for emp_str, ratio_dict in payload['ratios'].items():
                config['shift_ratios'][str(emp_str)] = {
                    'D': int(ratio_dict['D']),
                    'E': int(ratio_dict['E']),
                    'N': int(ratio_dict['N'])
                }

        # E. 엑셀식 고정 입력: OFF는 일반 휴무, 휴가만 평균 계산에서 제외
        if 'fixed_shifts' in payload:
            fixed_shifts = {}
            fixed_vacations = set(tuple(vacation) for vacation in config.get('vacations', []))
            for emp_str, employee_shifts in payload['fixed_shifts'].items():
                fixed_shifts[str(int(emp_str))] = {}
                for day, shift in employee_shifts.items():
                    normalized_shift = str(shift).strip().upper()
                    if normalized_shift in ('D', 'E', 'N', 'OFF', '휴가'):
                        employee = int(emp_str)
                        day_index = int(day)
                        fixed_shifts[str(employee)][str(day_index)] = normalized_shift
                        if normalized_shift == '휴가':
                            fixed_vacations.add((employee, day_index))
            config['fixed_shifts'] = fixed_shifts
            config['vacations'] = [list(vacation) for vacation in sorted(fixed_vacations)]
    else:
        print("--- 💻 로컬/기본 환경 감지: config.json 원본 설정으로 실행합니다 ---")

    print("--- 설정 로드 완료 ---")
    print(f"{config.get('num_employees', 10)}명의 직원을 대상으로 {config.get('num_days', 31)}일간의 스케줄링을 진행합니다.")
    print(f"솔버 제한 시간: {config.get('solver_time_limit', 1000)}초")

    conflicts = find_fixed_input_conflicts(config)
    if conflicts:
        print("--- 고정 입력 충돌: 솔버 실행을 중단합니다 ---")
        for conflict in conflicts:
            print(conflict)
        with open('schedule_result.json', 'w', encoding='utf-8') as f:
            json.dump({"status": "INPUT_CONFLICT", "errors": conflicts, "schedule": {}, "stats": {}}, f, ensure_ascii=False, indent=2)
        return

    print("--- 솔버 시작 ---")

    # 3. 솔버 실행
    status, objective_value, solution, expected_hours = solve_monthly_crew_schedule(config)

    print(f"--- 솔버 종료 ---")
    print(f"상태: {status}")
    
    if status in ('OPTIMAL', 'FEASIBLE'):
        print(f"솔버가 보고한 목적 함수 값: {objective_value:.2f}")

        # [터미널 출력용] 검증 및 프린트
        violations = validate_schedule(config, solution)
        if violations:
            print("\n--- 검증 실패 ---")
            for v in violations: print(v)
        else:
            print("\n--- 검증 성공: 모든 하드 제약 조건을 만족합니다. ---")
            
        print_schedule(config, solution, expected_hours)

        # 4. [핵심] 웹 UI 표시를 위한 JSON 결과 파일 저장
        output_data = {
            "status": status,
            "schedule": {},
            "stats": {}
        }
        num_days = config['num_days']
        shift_hours_map = {s: config['shifts'][s]['hours'] for s in config['shifts']}
        
        for e in range(config.get('num_employees', 10)):
            # 근무표 저장 (휴무면 '-')
            output_data["schedule"][e] = {d: (solution.get(e, {}).get(d) if solution.get(e, {}).get(d) != 'off' else '-') for d in range(num_days)}
            
            # 통계 계산
            total_hours = sum(shift_hours_map.get(solution.get(e, {}).get(d, 'off'), 0) for d in range(num_days))
            d_count = sum(1 for d in range(num_days) if solution.get(e, {}).get(d) == 'D')
            e_count = sum(1 for d in range(num_days) if solution.get(e, {}).get(d) == 'E')
            n_count = sum(1 for d in range(num_days) if solution.get(e, {}).get(d) == 'N')
            
            output_data["stats"][e] = {
                "avg_hours": round(total_hours / num_days * 7, 1),
                "D": d_count, "E": e_count, "N": n_count
            }
            
        with open('schedule_result.json', 'w', encoding='utf-8') as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
            
        print("✅ schedule_result.json 저장 완료 (웹 화면에서 읽어갈 준비 끝!)")
    else:
        print("주어진 제약 조건 하에서 유효한 스케줄을 찾을 수 없습니다.")

def print_schedule(config, solution, expected_hours):
    """
    최종 스케줄을 읽기 쉬운 형식으로 출력합니다.
    """
    num_days = config['num_days']
    num_employees = config['num_employees']
    crewX_periods = {int(k): tuple(v) for k, v in config['crewX_periods'].items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_hours = {s: config['shifts'][s]['hours'] for s in config['shifts']}

    print("\n--- Crew 휴식 기간 및 휴가 정보 ---")
    for e in range(num_employees):
        start_d, end_d = crewX_periods.get(e, (-1, -2))
        if 0 <= start_d <= end_d < num_days:
            print(f"  직원 {e:2d} | 휴식: {start_d + 1:2d}일 - {end_d + 1:2d}일")
    
    my_vacations = {e: [] for e in range(num_employees)}
    for e, d in vacations:
        my_vacations[e].append(d + 1)
    for e, days in my_vacations.items():
        if days:
            print(f"  직원 {e:2d} | 휴가일: {sorted(days)}")

    print("\n--- 월간 스케줄 ---")
    header = f"{'직원':<8} |"
    for d in range(num_days):
        header += f" {d + 1:^2}"
    header += " | 통계"
    print(header)
    print("-" * len(header))

    for e in range(num_employees):
        schedule_str = ""
        for d in range(num_days):
            if (e, d) in vacations:
                schedule_str += f"{' V ':^3}"
            else:
                shift = solution.get(e, {}).get(d, 'ERR')
                schedule_str += f" {shift if shift != 'off' else '-':<1} "
        
        counts = {s: 0 for s in config['shifts']}
        total_hours = 0
        crew_hours = 0
        start_d, end_d = crewX_periods.get(e, (-1, -2))

        for d in range(num_days):
            shift = solution.get(e, {}).get(d)
            if shift and shift != 'off':
                counts[shift] += 1
                hours = shift_hours.get(shift, 0)
                total_hours += hours
                if 0 <= start_d <= end_d < num_days and (d < start_d or d > end_d):
                    crew_hours += hours
        
        num_vacation_days = len(my_vacations.get(e, []))
        effective_days = num_days - num_vacation_days
        weekly_avg = (7 * total_hours / effective_days) if effective_days > 0 else 0
        
        stats_str = (
            f"목표: {expected_hours.get(e, 0):3d}h, "
            f"Crew: {crew_hours:3d}h, "
            f"평균: {weekly_avg:.1f}h/주 "
            f"(D:{counts['D']}, E:{counts['E']}, N:{counts['N']})"
        )
        print(f"직원 {e:2d}    |{schedule_str} | {stats_str}")

if __name__ == '__main__':
    main()