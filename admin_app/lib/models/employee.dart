/// Employee Hub models. Mirrors `server/app/employee.py`.
library;

class Benefit {
  final String name;
  final String kind;
  final String summary;
  final String status;

  const Benefit({
    required this.name,
    required this.kind,
    required this.summary,
    required this.status,
  });

  factory Benefit.fromJson(Map<String, dynamic> json) => Benefit(
    name: json['name'] as String? ?? '',
    kind: json['kind'] as String? ?? '',
    summary: json['summary'] as String? ?? '',
    status: json['status'] as String? ?? 'enrolled',
  );
}

class EmployeeProfile {
  final String uid;
  final String name;
  final double ptoBalanceDays;
  final double ptoAccrualPerMonth;
  final String nextCompanyHoliday;
  final List<Benefit> benefits;

  const EmployeeProfile({
    required this.uid,
    required this.name,
    required this.ptoBalanceDays,
    required this.ptoAccrualPerMonth,
    required this.nextCompanyHoliday,
    required this.benefits,
  });

  factory EmployeeProfile.fromJson(Map<String, dynamic> json) =>
      EmployeeProfile(
        uid: json['uid'] as String,
        name: json['name'] as String? ?? '',
        ptoBalanceDays: (json['pto_balance_days'] as num? ?? 0).toDouble(),
        ptoAccrualPerMonth:
            (json['pto_accrual_per_month'] as num? ?? 0).toDouble(),
        nextCompanyHoliday: json['next_company_holiday'] as String? ?? '',
        benefits: (json['benefits'] as List<dynamic>? ?? const [])
            .map((e) => Benefit.fromJson(e as Map<String, dynamic>))
            .toList(),
      );
}

class PtoRequest {
  final String id;
  final String startDate;
  final String endDate;
  final double days;
  final String note;
  final String state; // approved | pending | denied
  final String reason;
  final String taskId;
  final String decidedBy;

  const PtoRequest({
    required this.id,
    required this.startDate,
    required this.endDate,
    required this.days,
    required this.note,
    required this.state,
    required this.reason,
    required this.taskId,
    required this.decidedBy,
  });

  factory PtoRequest.fromJson(Map<String, dynamic> json) => PtoRequest(
    id: json['id'] as String,
    startDate: json['start_date'] as String? ?? '',
    endDate: json['end_date'] as String? ?? '',
    days: (json['days'] as num? ?? 0).toDouble(),
    note: json['note'] as String? ?? '',
    state: json['state'] as String? ?? 'pending',
    reason: json['reason'] as String? ?? '',
    taskId: json['task_id'] as String? ?? '',
    decidedBy: json['decided_by'] as String? ?? '',
  );
}

class HrCitation {
  final String id;
  final String title;

  const HrCitation({required this.id, required this.title});

  factory HrCitation.fromJson(Map<String, dynamic> json) => HrCitation(
    id: json['id'] as String? ?? '',
    title: json['title'] as String? ?? '',
  );
}

class HrChatReply {
  final String reply;
  final List<HrCitation> citations;
  final String backend;

  const HrChatReply({
    required this.reply,
    required this.citations,
    required this.backend,
  });

  factory HrChatReply.fromJson(Map<String, dynamic> json) => HrChatReply(
    reply: json['reply'] as String? ?? '',
    citations: (json['citations'] as List<dynamic>? ?? const [])
        .map((e) => HrCitation.fromJson(e as Map<String, dynamic>))
        .toList(),
    backend: json['backend'] as String? ?? '',
  );
}
