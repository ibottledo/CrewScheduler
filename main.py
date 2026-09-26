import json
import os
import calendar
from scheduler import solve_monthly_crew_schedule
from validation import validate_schedule


def find_fixed_input_conflicts(config):
    """고정 입력만으로 즉시 판별할 수 있는 충돌을 찾습니다."""
    conflicts = []
    crew_break_periods = config.get('crewX_periods', {})
    for employee, period in crew_break_periods.items():
        if not isinstance(period, (list, tuple)) or len(period) != 2:
            conflicts.append(f"직원 {employee}번: 휴식 기간은 시작일과 종료일 두 값이어야 합니다.")
            continue

        start_day, end_day = period
        if start_day == -1 and end_day == -2:
            continue
        if not (0 <= start_day <= end_day < config['num_days']):
            conflicts.append(
                f"직원 {employee}번: 휴식 기간은 1일부터 {config['num_days']}일 사이의 유효한 구간이어야 합니다."
            )

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
    duration_conflicts = []
    config.setdefault('full_month_crew', {})
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
            
        # C. 전체 기간 Crew 여부 적용
        if 'full_month_crew' in payload:
            for emp_str, enabled in payload['full_month_crew'].items():
                config['full_month_crew'][str(int(emp_str))] = bool(enabled)

        # D. 크루 휴식 기간 적용
        if 'durations' in payload:
            for emp_str, period in payload['durations'].items():
                emp_int = int(emp_str)
                if config['full_month_crew'].get(str(emp_int), False):
                    config['crewX_periods'][str(emp_int)] = [-1, -2]
                    continue
                if not isinstance(period, (list, tuple)) or len(period) != 2:
                    duration_conflicts.append(f"직원 {emp_int}번: 휴식 기간은 시작일과 종료일 두 값이어야 합니다.")
                    continue
                start_day, end_day = period
                try:
                    start_day = int(start_day)
                    end_day = int(end_day)
                except (TypeError, ValueError):
                    duration_conflicts.append(f"직원 {emp_int}번: 휴식 기간은 숫자로 입력해야 합니다.")
                    continue

                if start_day == 0 and end_day == 0:
                    config['crewX_periods'][str(emp_int)] = [-1, -2]
                else:
                    config['crewX_periods'][str(emp_int)] = [start_day - 1, end_day - 1]

        # E. 근무 비율 적용
        if 'ratios' in payload:
            for emp_str, ratio_dict in payload['ratios'].items():
                config['shift_ratios'][str(emp_str)] = {
                    'D': int(ratio_dict['D']),
                    'E': int(ratio_dict['E']),
                    'N': int(ratio_dict['N'])
                }

        # F. 엑셀식 고정 입력: OFF는 일반 휴무, 휴가만 평균 계산에서 제외
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

    conflicts = duration_conflicts + find_fixed_input_conflicts(config)
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
            vacation_days = sum(1 for d in range(num_days) if (e, d) in config['vacations'])
            effective_days = num_days - vacation_days
            d_count = sum(1 for d in range(num_days) if solution.get(e, {}).get(d) == 'D')
            e_count = sum(1 for d in range(num_days) if solution.get(e, {}).get(d) == 'E')
            n_count = sum(1 for d in range(num_days) if solution.get(e, {}).get(d) == 'N')
            
            output_data["stats"][e] = {
                "avg_hours": round(total_hours / effective_days * 7, 1) if effective_days > 0 else 0,
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
    full_month_crew = {int(k): bool(v) for k, v in config.get('full_month_crew', {}).items()}
    vacations = [tuple(v) for v in config['vacations']]
    shift_hours = {s: config['shifts'][s]['hours'] for s in config['shifts']}

    print("\n--- Crew 휴식 기간 및 휴가 정보 ---")
    for e in range(num_employees):
        start_d, end_d = crewX_periods.get(e, (-1, -2))
        if full_month_crew.get(e, False):
            print(f"  직원 {e:2d} | 휴식 없음: 1일 - {num_days:2d}일 전체 Crew")
        elif 0 <= start_d <= end_d < num_days:
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
        is_full_month_crew = full_month_crew.get(e, False)

        for d in range(num_days):
            shift = solution.get(e, {}).get(d)
            if shift and shift != 'off':
                counts[shift] += 1
                hours = shift_hours.get(shift, 0)
                total_hours += hours
                if is_full_month_crew or (0 <= start_d <= end_d < num_days and (d < start_d or d > end_d)):
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