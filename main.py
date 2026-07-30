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
    
    return total_calculated_penalty

def main():
    """
    Crew 스케줄링 프로세스를 실행하는 메인 함수.
    """
    # 1. 설정 불러오기
    with open('config.json', 'r', encoding='utf-8') as f:
        config = json.load(f)

    # --- [추가/수정됨] 데이터 유입 경로 확인 ---
    payload_raw = os.environ.get('PAYLOAD')  # 웹페이지(JS)에서 보내는 JSON 데이터
    year_str = os.environ.get('YEAR')        # Actions 수동 실행 시 입력하는 환경변수

    # A. 웹페이지 달력(GitHub Pages)에서 버튼을 눌러 실행된 경우
    if payload_raw and payload_raw.strip() not in ('', '{}'):
        print("--- 🌐 웹(Payload) 요청 감지: 웹 UI 입력값으로 설정 업데이트 ---")
        try:
            payload = json.loads(payload_raw)
            
            # 1. 연/월 적용
            if 'year' in payload and 'month' in payload:
                year = int(payload['year'])
                month = int(payload['month'])
                config['num_days'] = calendar.monthrange(year, month)[1]
            
            # 2. 웹에서 선택한 휴가일 적용
            if 'vacations' in payload:
                new_vacations = []
                for emp, day in payload['vacations']:
                    new_vacations.append([int(emp), int(day) - 1]) # 파이썬은 0-indexed 이므로 -1
                config['vacations'] = new_vacations
                
                for emp, day in new_vacations:
                    print(f"직원 {emp}의 휴가일: {day + 1}일")
                
            # 3. 투입 기간(durations) 업데이트 추가
            if 'durations' in payload:
                # payload['durations'] 형태: {"0": [1, 31], "1": [0, 0]} (0,0 은 off)
                for emp_str, period in payload['durations'].items():
                    emp_int = int(emp_str)
                    start_day, end_day = period
                    
                    if start_day == 0:  # 투입 안 함 (Off) 체크된 경우
                        config['crew_periods'][str(emp_int)] = [-1, -2]
                    else:
                        # 0-indexed로 맞춰서 config 덮어쓰기
                        config['crew_periods'][str(emp_int)] = [start_day - 1, end_day - 1]

                for emp_str, period in payload['durations'].items():
                    start_day, end_day = period
                    if start_day == 0:
                        print(f"직원 {emp_str}은 투입되지 않습니다.")
                    else:
                        print(f"직원 {emp_str}의 투입 기간: {start_day}일 ~ {end_day}일")

            # 4. 근무 비율(ratios) 업데이트 추가
            if 'ratios' in payload:
                # payload['ratios'] 형태: {"0": {"D": 2, "E": 1, "N": 2}}
                for emp_str, ratio_dict in payload['ratios'].items():
                    config['shift_ratios'][str(emp_str)] = {
                        'D': int(ratio_dict['D']),
                        'E': int(ratio_dict['E']),
                        'N': int(ratio_dict['N'])
                    }

                for emp_str, ratio_dict in payload['ratios'].items():
                    print(f"직원 {emp_str}의 근무 비율: D:{ratio_dict['D']} E:{ratio_dict['E']} N:{ratio_dict['N']}")
            
        except json.JSONDecodeError:
            print("경고: Payload 형식이 올바른 JSON이 아닙니다.")

    # B. 기존에 구현해두신 GitHub Actions 수동(workflow_dispatch) 실행인 경우
    elif year_str is not None:
        print("--- ⚙️ GitHub Actions 환경 감지: 수동 입력값으로 설정 업데이트 ---")
        
        month_str = os.environ.get('MONTH')
        if year_str and month_str:
            year = int(year_str)
            month = int(month_str)
            config['num_days'] = calendar.monthrange(year, month)[1]

        shift_ratio_str = os.environ.get('SHIFT_RATIO')
        if shift_ratio_str:
            try:
                ratios = [int(r) for r in shift_ratio_str.split(':')]
                if len(ratios) == 3:
                    new_ratio = {'D': ratios[0], 'E': ratios[1], 'N': ratios[2]}
                    num_employees = config.get('num_employees', 10)
                    config['shift_ratios'] = {str(i): new_ratio for i in range(num_employees)}
                    print(f"근무 비율이 모든 직원에게 {shift_ratio_str} (D:E:N)으로 적용되었습니다.")
                else:
                    print(f"경고: 근무 비율({shift_ratio_str}) 형식이 잘못되었습니다.")
            except ValueError:
                print(f"경고: 근무 비율({shift_ratio_str})에 오류가 있습니다.")

        num_employees_from_config = config.get('num_employees', 10)
        new_crew_periods = {i: [-1, -2] for i in range(num_employees_from_config)}
        new_vacations = []

        for i in range(10):
            duration = os.environ.get(f'CREW_{i}_DURATION')
            vacation = os.environ.get(f'CREW_{i}_VACATION')
            ratio = os.environ.get(f'CREW_{i}_RATIO')
            
            if duration and '~' in duration:
                try:
                    start_day, end_day = duration.split('~')
                    new_crew_periods[i] = [int(start_day) - 1, int(end_day) - 1]
                except (ValueError, IndexError):
                    print(f"경고: 크루 {i}의 기간({duration}) 오류.")
            
            if vacation:
                for day in vacation.split(','):
                    day = day.strip()
                    if day:
                        try:
                            new_vacations.append([i, int(day) - 1])
                        except ValueError:
                            pass

            if ratio and ':' in ratio:
                try:
                    d_ratio, e_ratio, n_ratio = map(int, ratio.split(':'))
                    config['shift_ratios'][str(i)] = {'D': d_ratio, 'E': e_ratio, 'N': n_ratio}
                except ValueError:
                    pass

        config['crew_periods'] = new_crew_periods
        config['vacations'] = new_vacations

    else:
        print("--- 💻 로컬 환경 감지: config.json 원본 설정으로 실행합니다 ---")

    print("--- 설정 로드 완료 ---")
    print(f"{config['num_employees']}명의 직원을 대상으로 {config['num_days']}일간의 스케줄링을 진행합니다.")
    print("--- 솔버 시작 ---")

    # 2. 스케줄링 엔진 실행
    status, objective_value, solution, expected_hours = solve_monthly_crew_schedule(config)

    print(f"--- 솔버 종료 ---")
    print(f"상태: {status}")
    if status in ('OPTIMAL', 'FEASIBLE'):
        print(f"솔버가 보고한 목적 함수 값: {objective_value:.2f}")

        # 3. 솔루션 검증
        violations = validate_schedule(config, solution)
        if violations:
            print("\n--- 검증 실패 ---")
            for v in violations:
                print(v)
        else:
            print("\n--- 검증 성공: 모든 하드 제약 조건을 만족합니다. ---")

        # 4. 총 페널티 직접 계산
        calculated_penalty = calculate_total_penalty(config, solution)
        print(f"직접 계산한 총 페널티: {calculated_penalty:.2f}")

        # 5. [디버그] N-N 위반 카운트
        nn_violations_count = 0
        for e in range(config['num_employees']):
            employee_nn_count = 0
            for d in range(config['num_days'] - 1):
                if solution.get(e, {}).get(d) == 'N' and solution.get(e, {}).get(d+1) == 'N':
                    employee_nn_count += 1
            if employee_nn_count > 1:
                print(f"디버그: 직원 {e}가 {employee_nn_count}개의 'N N' 위반을 가집니다.")
            nn_violations_count += employee_nn_count
        print(f"디버그: 스케줄 내 총 'N N' 시퀀스 수: {nn_violations_count}")

        # 6. 스케줄 출력
        print_schedule(config, solution, expected_hours)
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