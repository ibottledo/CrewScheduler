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

    crew_periods = {int(k): tuple(v) for k, v in config['crew_periods'].items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_ratios = {int(k): v for k, v in config['shift_ratios'].items()}
    
    penalties_config = config['penalties']

    # 솔루션에서 특정 직원과 날짜의 근무를 가져오는 헬퍼 함수
    def get_shift(e, d):
        return solution.get(e, {}).get(d, 'off')

    # --- Crew 멤버에 대한 페널티 ---
    for e in all_employees:
        start_d, end_d = crew_periods.get(e, (-1, -2)) # .get()으로 안전하게 접근
        is_crew_member = (end_d >= start_d)

        if is_crew_member:
            # 주간 평균 40시간 초과에 대한 페널티
            current_total_hours = sum(shift_hours.get(get_shift(e, d), 0) for d in all_days if get_shift(e, d) != 'off')
            num_vacation_days = sum(1 for d in all_days if (e, d) in vacations)
            effective_days = num_days - num_vacation_days
            
            if effective_days > 0:
                hours_over_threshold = max(0, (7 * current_total_hours) - (40 * effective_days))
                total_calculated_penalty += hours_over_threshold * PENALTY_PRIORITY_MAP[penalties_config['crew_over_40h_avg_priority']]

            # 초과 투입 페널티 (인력 부족은 이제 하드 제약 조건임)
            my_crew_days = max(0, end_d - start_d + 1)
            my_expected_hours = 0
            if my_crew_days > 0:
                vacation_days_in_crew_period = sum(1 for d in range(start_d, end_d + 1) if (e, d) in vacations)
                effective_crew_days_in_period = my_crew_days - vacation_days_in_crew_period
                if effective_crew_days_in_period > 0:
                    my_expected_hours = -int(-(effective_crew_days_in_period * 40 / 7.0))
            
            current_crew_hours = sum(shift_hours.get(get_shift(e, d), 0) 
                                     for d in range(start_d, end_d + 1) if 0 <= d < num_days and get_shift(e, d) != 'off')
            
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

    # 최대 초과 투입 시간에 대한 페널티
    current_over_values = []
    for e in all_employees:
        start_d, end_d = crew_periods.get(e, (-1, -2))
        is_crew_member = (end_d >= start_d)
        if is_crew_member:
            my_expected_hours = 0
            if max(0, end_d - start_d + 1) > 0:
                vacation_days_in_crew_period = sum(1 for d in range(start_d, end_d + 1) if (e, d) in vacations)
                effective_crew_days_in_period = max(0, end_d - start_d + 1) - vacation_days_in_crew_period
                if effective_crew_days_in_period > 0:
                    my_expected_hours = -int(-(effective_crew_days_in_period * 40 / 7.0))
            
            current_crew_hours = sum(shift_hours.get(get_shift(e, d), 0) 
                                     for d in range(start_d, end_d + 1) if 0 <= d < num_days and get_shift(e, d) != 'off')
            current_over_values.append(max(0, current_crew_hours - my_expected_hours))
    
    if current_over_values:
        max_over = max(current_over_values)
        total_calculated_penalty += max_over * PENALTY_PRIORITY_MAP[penalties_config['max_over_staffing_priority']]
        
    # 비-Crew 근무의 공정성에 대한 페널티
    current_non_crew_hours = []
    for e in all_employees:
        start_d, end_d = crew_periods.get(e, (-1, -2))
        is_crew_member = (end_d >= start_d)
        if not is_crew_member:
            current_non_crew_hours.append(sum(shift_hours.get(get_shift(e, d), 0) for d in all_days if get_shift(e, d) != 'off'))
    
    if current_non_crew_hours:
        max_nc = max(current_non_crew_hours)
        min_nc = min(current_non_crew_hours)
        total_calculated_penalty += (max_nc - min_nc) * PENALTY_PRIORITY_MAP[penalties_config['fairness_of_non_crew_work_priority']]
        total_calculated_penalty += sum(current_non_crew_hours) * PENALTY_PRIORITY_MAP[penalties_config['non_crew_work_priority']]
        
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
            
        # C. 크루 투입 기간 적용
        if 'durations' in payload:
            for emp_str, period in payload['durations'].items():
                emp_int = int(emp_str)
                start_day, end_day = period
                if start_day == 0:
                    config['crew_periods'][str(emp_int)] = [-1, -2]
                else:
                    config['crew_periods'][str(emp_int)] = [start_day - 1, end_day - 1]

        # D. 근무 비율 적용
        if 'ratios' in payload:
            for emp_str, ratio_dict in payload['ratios'].items():
                config['shift_ratios'][str(emp_str)] = {
                    'D': int(ratio_dict['D']),
                    'E': int(ratio_dict['E']),
                    'N': int(ratio_dict['N'])
                }
    else:
        print("--- 💻 로컬/기본 환경 감지: config.json 원본 설정으로 실행합니다 ---")

    print("--- 설정 로드 완료 ---")
    print(f"{config.get('num_employees', 10)}명의 직원을 대상으로 {config.get('num_days', 31)}일간의 스케줄링을 진행합니다.")
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

        # 4. 🚀 [핵심] 웹 UI 표시를 위한 JSON 결과 파일 저장
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
    crew_periods = {int(k): tuple(v) for k, v in config['crew_periods'].items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_hours = {s: config['shifts'][s]['hours'] for s in config['shifts']}

    print("\n--- Crew 기간 및 휴가 정보 ---")
    for e in range(num_employees):
        start_d, end_d = crew_periods.get(e, (-1, -2))
        if end_d >= start_d:
            print(f"  직원 {e:2d} | 기간: {start_d + 1:2d}일 - {end_d + 1:2d}일")
    
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
        start_d, end_d = crew_periods.get(e, (-1, -2))

        for d in range(num_days):
            shift = solution.get(e, {}).get(d)
            if shift and shift != 'off':
                counts[shift] += 1
                hours = shift_hours.get(shift, 0)
                total_hours += hours
                if start_d <= d <= end_d:
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