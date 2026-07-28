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
            # 비-Crew 근무는 공정성 및 총 비-crew 시간 페널티로 처리됨
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

    # 최대 초과 투입 시간에 대한 페널티 (이미 계산된 'over' 변수들의 최대값)
    # 솔루션으로부터 max_over를 다시 계산해야 함
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

    # --- GitHub Actions 입력값 처리 (환경 변수가 있을 경우) ---
    year_str = os.environ.get('YEAR')
    month_str = os.environ.get('MONTH')

    # Actions에서 실행되었는지 확인 (YEAR 환경변수 존재 여부로 판단)
    is_running_on_actions = year_str is not None

    if is_running_on_actions:
        print("--- GitHub Actions 환경 감지: 입력값으로 설정 업데이트 ---")
        
        all_crews_input_data = []
        for i in range(10):
            duration = os.environ.get(f'CREW_{i}_DURATION')
            vacation = os.environ.get(f'CREW_{i}_VACATION')
            if duration or vacation:
                all_crews_input_data.append({'id': str(i), 'duration': duration, 'vacation': vacation})

        # 연/월이 입력되었다면, 해당 월의 일자로 num_days를 업데이트
        if year_str and month_str:
            year = int(year_str)
            month = int(month_str)
            config['num_days'] = calendar.monthrange(year, month)[1]

        # 크루별 기간(crew_periods)과 휴가(vacations)를 새 값으로 덮어쓰기
        if all_crews_input_data:
            new_crew_periods = {}
            new_vacations = []
            for data in all_crews_input_data:
                crew_id = int(data['id'])
                
                if data['duration'] and '~' in data['duration']:
                    try:
                        start_day, end_day = data['duration'].split('~')
                        new_crew_periods[str(crew_id)] = [int(start_day) - 1, int(end_day) - 1]
                    except ValueError:
                        print(f"경고: 크루 {crew_id}의 기간({data['duration']}) 형식이 잘못되었습니다.")

                if data['vacation']:
                    for day in data['vacation'].split(','):
                        day = day.strip()
                        if day:
                            try:
                                new_vacations.append([crew_id, int(day) - 1])
                            except ValueError:
                                print(f"경고: 크루 {crew_id}의 휴가일({day}) 형식이 잘못되었습니다.")
            
            config['crew_periods'] = new_crew_periods
            config['vacations'] = new_vacations

    print("--- 설정 로드 완료 ---")
    print(f"{config['num_employees']}명의 직원을 대상으로 {config['num_days']}일간의 스케줄링을 진행합니다.")
    print(f"솔버 시간 제한: {config['solver_time_limit']}초.")
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

        # 4. 총 페널티를 직접 계산하고 출력
        calculated_penalty = calculate_total_penalty(config, solution)
        print(f"직접 계산한 총 페널티: {calculated_penalty:.2f}")

        # 5. [디버그] 솔루션에서 NN 위반을 직접 카운트하여 하드 제약 조건 확인
        nn_violations_count = 0
        for e in range(config['num_employees']):
            employee_nn_count = 0
            for d in range(config['num_days'] - 1):
                if solution.get(e, {}).get(d) == 'N' and solution.get(e, {}).get(d+1) == 'N':
                    employee_nn_count += 1
            if employee_nn_count > 1:
                print(f"디버그: 직원 {e}가 {employee_nn_count}개의 'N N' 위반을 가집니다 (하드 제약 조건에 의해 1 이하여야 함).")
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
        
        # 통계 계산
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