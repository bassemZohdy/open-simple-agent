import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

export const supportedLocales = ["en", "ar"] as const;
export type Locale = (typeof supportedLocales)[number];

type InterpolationValues = Record<string, string | number>;

const localeStorageKey = "osa-control-panel-locale";

const arabicMessages: Record<string, string> = {
  "Skip to content": "تخطّي إلى المحتوى",
  "Control Panel": "لوحة التحكم",
  "Bearer token active": "رمز Bearer مفعّل",
  "Anonymous API mode": "وضع API المجهول",
  Disconnect: "قطع الاتصال",
  "Connect token": "ربط رمز",
  "Bearer access token": "رمز وصول Bearer",
  "Paste a short-lived token": "ألصق رمزًا قصير الأجل",
  "Use token": "استخدام الرمز",
  "Show token": "إظهار الرمز",
  "Hide token": "إخفاء الرمز",
  "Stored only in this browser tab/session; never written to OSA configuration.":
    "يُحفظ هذا الرمز في علامة التبويب/الجلسة الحالية فقط، ولا يُكتب في إعدادات OSA.",
  "Control Panel sections": "أقسام لوحة التحكم",
  Language: "اللغة",
  Agents: "الوكلاء",
  Templates: "القوالب",
  Resources: "الموارد",
  Deployments: "عمليات النشر",
  Console: "وحدة التحكم",
  Health: "الحالة",
  Audit: "التدقيق",
  "Managed agents": "الوكلاء المُدارون",
  "Browse Control Plane records without routing invocation traffic through the Control Plane.":
    "استعرض سجلات مستوى التحكم دون تمرير حركة الاستدعاء عبر مستوى التحكم.",
  Search: "بحث",
  Status: "الحالة",
  "Name or description": "الاسم أو الوصف",
  "Clear search": "مسح البحث",
  "All statuses": "كل الحالات",
  "Apply filters": "تطبيق عوامل التصفية",
  "Close create": "إغلاق الإنشاء",
  "Create agent": "إنشاء وكيل",
  "New agent": "وكيل جديد",
  "Cloning {name}": "استنساخ {name}",
  "Metadata is copied. Agent definitions are write-only in the Control Plane, so choose a template or paste a definition for the copy.":
    "تم نسخ البيانات الوصفية. تعريفات الوكلاء للكتابة فقط في مستوى التحكم، لذا اختر قالبًا أو ألصق تعريفًا للنسخة.",
  Name: "الاسم",
  Description: "الوصف",
  "e.g. support-agent": "مثال: support-agent",
  "What does this agent do?": "ما وظيفة هذا الوكيل؟",
  "Configuration source": "مصدر الإعدادات",
  "Empty draft (no definition)": "مسودة فارغة (بلا تعريف)",
  "Built-in template": "قالب مضمّن",
  "JSON definition": "تعريف JSON",
  Template: "القالب",
  "Select a template…": "اختر قالبًا…",
  "Definition (JSON)": "التعريف (JSON)",
  "Create failed": "فشل الإنشاء",
  "Creating…": "جارٍ الإنشاء…",
  Cancel: "إلغاء",
  "Loading agents…": "جارٍ تحميل الوكلاء…",
  "Agent list unavailable": "قائمة الوكلاء غير متاحة",
  Retry: "إعادة المحاولة",
  "No agents found": "لم يتم العثور على وكلاء",
  "Create an agent through the API or adjust the current filters.": "أنشئ وكيلًا عبر API أو عدّل عوامل التصفية الحالية.",
  "No description": "لا يوجد وصف",
  Version: "الإصدار",
  Runtime: "بيئة التشغيل",
  Skills: "المهارات",
  Agent: "الوكيل",
  Model: "النموذج",
  Tool: "الأداة",
  Skill: "المهارة",
  Mcp: "MCP",
  MemoryPolicy: "سياسة الذاكرة",
  provider: "المزوّد",
  model: "النموذج",
  transport: "النقل",
  scope: "النطاق",
  enabled: "مفعّل",
  "retention days": "أيام الاحتفاظ",
  "{count} matching agents": "{count} وكيل مطابق",
  "{count} matching resources": "{count} مورد مطابق",
  "{count} versions": "{count} إصدار",
  "{count} deployments": "{count} عملية نشر",
  "{count} templates": "{count} قالب",
  "{count} external agents": "{count} وكيل خارجي",
  "{count} skill(s)": "{count} مهارة",
  "matching agents": "وكلاء مطابقون",
  "matching resources": "موارد مطابقة",
  "Agent detail": "تفاصيل الوكيل",
  "Agent identifier is missing": "معرّف الوكيل مفقود",
  "Unable to load the agent to clone": "تعذر تحميل الوكيل لاستنساخه",
  "Agent {action} successfully.": "تم {action} الوكيل بنجاح.",
  "Unable to {action} agent": "تعذر {action} الوكيل",
  "Version {version} created successfully.": "تم إنشاء الإصدار {version} بنجاح.",
  "Safe definition for version {version}": "التعريف الآمن للإصدار {version}",
  "← Back to agents": "← العودة إلى الوكلاء",
  "Loading agent details…": "جارٍ تحميل تفاصيل الوكيل…",
  "Agent details unavailable": "تفاصيل الوكيل غير متاحة",
  "Action failed": "فشل الإجراء",
  Lifecycle: "دورة الحياة",
  "Manage agent": "إدارة الوكيل",
  "Transitions are validated by the Control Plane and only allowed for the current agent state.":
    "يتحقق مستوى التحكم من الانتقالات ولا يسمح إلا بما يناسب حالة الوكيل الحالية.",
  Activate: "تفعيل",
  Disable: "تعطيل",
  Archive: "أرشفة",
  activate: "التفعيل",
  disable: "التعطيل",
  archive: "الأرشفة",
  activated: "تفعيل",
  "Clone agent": "استنساخ الوكيل",
  "Confirm archive": "تأكيد الأرشفة",
  "Archive is terminal. Confirm this action?": "الأرشفة نهائية. هل تؤكد هذا الإجراء؟",
  "No further lifecycle actions are available.": "لا تتوفر إجراءات أخرى لدورة الحياة.",
  Configuration: "الإعدادات",
  "Agent metadata": "بيانات الوكيل الوصفية",
  "Agent ID": "معرّف الوكيل",
  "Current version": "الإصدار الحالي",
  Tenant: "المستأجر",
  "Shared scope": "النطاق المشترك",
  Labels: "التسميات",
  "View deployment history": "عرض سجل النشر",
  "Immutable snapshots": "لقطات غير قابلة للتغيير",
  "Version history": "سجل الإصدارات",
  "No change summary": "لا يوجد ملخص للتغيير",
  "Change summary": "ملخص التغيير",
  "e.g. 2.0.0": "مثال: 2.0.0",
  "What changed?": "ما الذي تغيّر؟",
  "Version creation failed": "فشل إنشاء الإصدار",
  "Create version": "إنشاء إصدار",
  "No version snapshots": "لا توجد لقطات إصدارات",
  "Create the first immutable snapshot from the current agent definition.": "أنشئ أول لقطة غير قابلة للتغيير من تعريف الوكيل الحالي.",
  Current: "حالي",
  Created: "تاريخ الإنشاء",
  "Created by": "أنشأه",
  "Control Plane": "مستوى التحكم",
  Snapshot: "اللقطة",
  "Definition available": "التعريف متاح",
  "No definition": "لا يوجد تعريف",
  "Loading snapshot…": "جارٍ تحميل اللقطة…",
  "Hide safe snapshot": "إخفاء اللقطة الآمنة",
  "View safe snapshot": "عرض اللقطة الآمنة",
  "This immutable definition is safe for inspection. Secret-like values are redacted before leaving the Control Plane.":
    "هذا التعريف غير القابل للتغيير آمن للفحص. تُحجب القيم الشبيهة بالأسرار قبل مغادرة مستوى التحكم.",
  "Redacted fields: {fields}": "الحقول المحجوبة: {fields}",
  "Snapshot unavailable": "اللقطة غير متاحة",
  "Operational oversight": "الإشراف التشغيلي",
  "Audit & metrics": "التدقيق والمقاييس",
  "Recent Control Plane audit events and bounded operational metrics.": "أحدث أحداث تدقيق مستوى التحكم والمقاييس التشغيلية المحدودة.",
  "shown of the {total} most recent events loaded": "المعروض من أصل {total} حدثًا حديثًا محمّلًا",
  loaded: "محمّل",
  Action: "الإجراء",
  "e.g. deployment or agent": "مثال: deployment أو agent",
  Limit: "الحد",
  Refresh: "تحديث",
  "Loading audit events…": "جارٍ تحميل أحداث التدقيق…",
  "Audit events unavailable": "أحداث التدقيق غير متاحة",
  "No audit events": "لا توجد أحداث تدقيق",
  "Control Plane operations are recorded here as they happen.": "تُسجّل عمليات مستوى التحكم هنا فور حدوثها.",
  Time: "الوقت",
  Actor: "الفاعل",
  Target: "الهدف",
  Detail: "التفاصيل",
  "Operational metrics": "المقاييس التشغيلية",
  "Prometheus metrics": "مقاييس Prometheus",
  "Refresh metrics": "تحديث المقاييس",
  "Loading metrics…": "جارٍ تحميل المقاييس…",
  "Metrics unavailable": "المقاييس غير متاحة",
  "No metrics recorded": "لم تُسجّل مقاييس",
  "Counters appear once the Control Plane handles traffic.": "تظهر العدادات بعد أن يعالج مستوى التحكم حركة المرور.",
  Metric: "المقياس",
  Value: "القيمة",
  "Raw Prometheus exposition": "بيانات Prometheus الخام",
  "Raw metrics exposition": "بيانات المقاييس الخام",
  "Recent audit events": "أحدث أحداث التدقيق",
  "Prometheus metric samples": "عينات مقاييس Prometheus",
  "{shown} shown of the {total} most recent events loaded": "المعروض {shown} من أصل {total} من أحدث الأحداث المحمّلة",
  Operations: "العمليات",
  "Checking readiness…": "جارٍ التحقق من الجاهزية…",
  "Control Plane unavailable": "مستوى التحكم غير متاح",
  "Readiness: {status}": "الجاهزية: {status}",
  ready: "جاهز",
  "Source: GET /health/ready": "المصدر: GET /health/ready",
  "Raw readiness payload": "بيانات الجاهزية الخام",
  "Invocation console": "وحدة الاستدعاء",
  "Test external A2A agents registered with the Control Plane.": "اختبر وكلاء A2A الخارجيين المسجلين في مستوى التحكم.",
  "external agents": "وكلاء خارجيون",
  "Loading external agents…": "جارٍ تحميل الوكلاء الخارجيين…",
  "External agents unavailable": "الوكلاء الخارجيون غير متاحين",
  "No external agents registered": "لا يوجد وكلاء خارجيون مسجلون",
  "Registered external A2A agents": "وكلاء A2A الخارجيون المسجلون",
  "No card name": "لا يوجد اسم للبطاقة",
  "Register an external A2A agent through the Control Plane API to test it here.": "سجّل وكيل A2A خارجيًا عبر API مستوى التحكم لاختباره هنا.",
  URL: "العنوان",
  Selected: "محدد",
  Test: "اختبار",
  "A2A test console": "وحدة اختبار A2A",
  "Send a message": "إرسال رسالة",
  "Timeout (seconds)": "المهلة (بالثواني)",
  "Clamped to 1–300 seconds; effective timeout: {seconds}s.": "تُحصر بين 1 و300 ثانية؛ المهلة الفعلية: {seconds}ث.",
  Message: "الرسالة",
  "What would you like to ask the remote agent?": "ماذا تريد أن تسأل الوكيل البعيد؟",
  "Invocation failed": "فشل الاستدعاء",
  "Invoking…": "جارٍ الاستدعاء…",
  "Invoke agent": "استدعاء الوكيل",
  Response: "الاستجابة",
  "Agent response": "استجابة الوكيل",
  "External agents are invoked through the A2A protocol via the Control Plane. Managed agents are invoked from the Deployments page on deployments that publish a runtime invoke URL (ADR-008).":
    "تُستدعى الوكلاء الخارجيون عبر بروتوكول A2A من خلال مستوى التحكم. أما الوكلاء المُدارون فيُستدعون من صفحة عمليات النشر عندما تنشر العملية عنوان استدعاء لبيئة التشغيل (ADR-008).",
  "Runtime catalogs": "كتالوجات بيئة التشغيل",
  "Tenant-scoped model, tool, skill, MCP, and memory-policy definitions returned by the Control Plane.": "تعريفات النماذج والأدوات والمهارات وMCP وسياسات الذاكرة ضمن نطاق المستأجر كما يعيدها مستوى التحكم.",
  "Resource kind": "نوع المورد",
  "Search {kind} names": "البحث في أسماء {kind}",
  "Resource name": "اسم المورد",
  "Loading {kind} resources…": "جارٍ تحميل موارد {kind}…",
  "{kind} resources unavailable": "موارد {kind} غير متاحة",
  "No {kind} resources found": "لم يتم العثور على موارد {kind}",
  "Unable to load {kind} resources": "تعذر تحميل موارد {kind}",
  "Adjust the search or register resources through the Control Plane API.": "عدّل البحث أو سجّل الموارد عبر API مستوى التحكم.",
  "View safe definition": "عرض التعريف الآمن",
  "Reusable definitions": "تعريفات قابلة لإعادة الاستخدام",
  "Built-in, read-only starting points exposed by the Control Plane.": "نقاط بداية مضمّنة للقراءة فقط يعرضها مستوى التحكم.",
  "Loading templates…": "جارٍ تحميل القوالب…",
  "Templates unavailable": "القوالب غير متاحة",
  "No templates available": "لا توجد قوالب متاحة",
  "The Control Plane returned an empty template catalog.": "أعاد مستوى التحكم كتالوج قوالب فارغًا.",
  "Agent template": "قالب وكيل",
  memory: "بذاكرة",
  stateless: "بلا حالة",
  "Memory policy": "سياسة الذاكرة",
  "Page not found": "الصفحة غير موجودة",
  "The requested Control Panel route does not exist.": "مسار لوحة التحكم المطلوب غير موجود.",
  "Back to agents": "العودة إلى الوكلاء",
  draft: "مسودة",
  active: "نشط",
  disabled: "معطل",
  archived: "مؤرشف",
  running: "يعمل",
  starting: "جارٍ البدء",
  failed: "فشل",
  stopped: "متوقف",
  healthy: "سليم",
  unreachable: "يتعذر الوصول",
  "Not configured": "غير مُعدّ",
  "Runtime operations": "عمليات بيئة التشغيل",
  "Launch versioned agents through the Control Plane and manage their lifecycle, status, and logs.": "أطلق الوكلاء ذوي الإصدارات عبر مستوى التحكم وأدر دورة حياتهم وحالتهم وسجلاتهم.",
  "Select an agent…": "اختر وكيلًا…",
  "Refresh history": "تحديث السجل",
  "Create an agent through the API to deploy it.": "أنشئ وكيلًا عبر API لنشره.",
  "Select an agent": "اختر وكيلًا",
  "Deployment history is scoped to one agent; choose it above to view and manage its deployments.": "سجل النشر خاص بوكيل واحد؛ اختره أعلاه لعرض عمليات النشر وإدارتها.",
  "Deployment history": "سجل النشر",
  "Deploying…": "جارٍ النشر…",
  "Deploy current version": "نشر الإصدار الحالي",
  "Loading deployments…": "جارٍ تحميل عمليات النشر…",
  "No deployments yet": "لا توجد عمليات نشر بعد",
  "Deploy the current agent version to start its runtime through the configured provider.": "انشر إصدار الوكيل الحالي لبدء بيئة تشغيله عبر المزوّد المُعدّ.",
  Deployment: "النشر",
  Actions: "الإجراءات",
  Manage: "إدارة",
  "Deployment detail": "تفاصيل النشر",
  "Deployment history unavailable": "سجل النشر غير متاح",
  "Runtime endpoint": "عنوان بيئة التشغيل",
  "Unable to {action} deployment": "تعذر {action} النشر",
  "Loading…": "جارٍ التحميل…",
  "Refresh status": "تحديث الحالة",
  "Refreshing…": "جارٍ التحديث…",
  Stop: "إيقاف",
  "Stopping…": "جارٍ الإيقاف…",
  Restart: "إعادة التشغيل",
  "Restarting…": "جارٍ إعادة التشغيل…",
  Rollback: "التراجع",
  "Rolling back…": "جارٍ التراجع…",
  "Confirm rollback": "تأكيد التراجع",
  "(previous version)": "(الإصدار السابق)",
  "Roll back to version {version}? This relaunches the deployment from an earlier immutable snapshot.": "هل تريد التراجع إلى الإصدار {version}؟ سيعيد هذا إطلاق النشر من لقطة سابقة غير قابلة للتغيير.",
  "Rollback version": "إصدار التراجع",
  "Leave empty for the previous version": "اتركه فارغًا للإصدار السابق",
  "Roll back to this version": "التراجع إلى هذا الإصدار",
  "Rollback relaunches this deployment from an earlier immutable version snapshot.": "يعيد التراجع إطلاق هذا النشر من لقطة إصدار سابقة غير قابلة للتغيير.",
  "Managed invocation": "استدعاء مُدار",
  "Test message": "رسالة اختبار",
  "Ask the deployed agent something": "اسأل الوكيل المنشور شيئًا",
  "Send test message": "إرسال رسالة اختبار",
  "Managed invocation output": "ناتج الاستدعاء المُدار",
  "Sent directly to the runtime endpoint using its own authentication; the Control Plane token is never forwarded.": "تُرسل مباشرة إلى عنوان بيئة التشغيل باستخدام مصادقتها الخاصة؛ ولا يُمرر رمز مستوى التحكم.",
  "Captured output": "الناتج الملتقط",
  Logs: "السجلات",
  "Tail lines": "الأسطر الأخيرة",
  "Load logs": "تحميل السجلات",
  "No logs loaded": "لم تُحمّل سجلات",
  "Load the bounded captured output for this deployment.": "حمّل الناتج الملتقط والمحدود لهذا النشر.",
  "No captured output": "لا يوجد ناتج ملتقط",
  "The deployment has not produced any captured log lines yet.": "لم ينتج النشر أي أسطر سجل ملتقطة بعد.",
  "Deployment log output": "ناتج سجل النشر",
  "Deployment {id} started for version {version}.": "بدأ النشر {id} للإصدار {version}.",
  "Deployment rolled back to version {version}.": "تم التراجع بالنشر إلى الإصدار {version}.",
  "Unable to load agents": "تعذر تحميل الوكلاء",
  "Unable to load agent details": "تعذر تحميل تفاصيل الوكيل",
  "Unable to create agent": "تعذر إنشاء الوكيل",
  "Unable to create version": "تعذر إنشاء الإصدار",
  "Unable to load the version snapshot": "تعذر تحميل لقطة الإصدار",
  "Unable to load audit events": "تعذر تحميل أحداث التدقيق",
  "Unable to load metrics": "تعذر تحميل المقاييس",
  "Unable to read Control Plane health": "تعذر قراءة حالة مستوى التحكم",
  "Unable to load external agents": "تعذر تحميل الوكلاء الخارجيين",
  "Unable to invoke the external agent": "تعذر استدعاء الوكيل الخارجي",
  "Unable to load templates": "تعذر تحميل القوالب",
  "Unable to load deployment history": "تعذر تحميل سجل النشر",
  "Unable to deploy deployment": "تعذر نشر النشر",
  "Unable to load deployment logs": "تعذر تحميل سجلات النشر",
  "Unable to reach the runtime endpoint": "تعذر الوصول إلى عنوان بيئة التشغيل",
  "Select an external agent to invoke": "اختر وكيلًا خارجيًا لاستدعائه",
  "A message is required": "الرسالة مطلوبة",
  "Agent name is required": "اسم الوكيل مطلوب",
  "A definition is required in definition mode": "التعريف مطلوب في وضع التعريف",
  "Definition is not valid JSON": "التعريف ليس JSON صالحًا",
  "Definition must be a JSON object": "يجب أن يكون التعريف كائن JSON",
  'Definition metadata.name must match the agent name "{name}"': 'يجب أن يطابق metadata.name في التعريف اسم الوكيل "{name}"',
  "Select a template or switch the configuration source to draft": "اختر قالبًا أو بدّل مصدر الإعدادات إلى مسودة",
  "Version is required": "الإصدار مطلوب",
};

function isLocale(value: string | null): value is Locale {
  return value !== null && (supportedLocales as readonly string[]).includes(value);
}

function readInitialLocale(): Locale {
  try {
    const stored = window.sessionStorage.getItem(localeStorageKey);
    if (isLocale(stored)) return stored;
  } catch {
    // Private browsing and blocked storage should not prevent the panel from loading.
  }
  const browserLanguages = navigator.languages.length > 0 ? navigator.languages : [navigator.language];
  return browserLanguages.some((language) => language.toLowerCase().startsWith("ar")) ? "ar" : "en";
}

function interpolate(template: string, values: InterpolationValues | undefined): string {
  if (!values) return template;
  return template.replace(/\{(\w+)\}/g, (match, key: string) => (key in values ? String(values[key]) : match));
}

export function translate(locale: Locale, key: string, values?: InterpolationValues): string {
  const message = locale === "ar" ? (arabicMessages[key] ?? key) : key;
  return interpolate(message, values);
}

interface LocaleContextValue {
  locale: Locale;
  setLocale: (nextLocale: Locale) => void;
  t: (key: string, values?: InterpolationValues) => string;
}

const LocaleContext = createContext<LocaleContextValue>({
  locale: "en",
  setLocale: () => undefined,
  t: (key, values) => interpolate(key, values),
});

export function LocaleProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(readInitialLocale);

  const setLocale = useCallback((nextLocale: Locale) => {
    setLocaleState(nextLocale);
    try {
      window.sessionStorage.setItem(localeStorageKey, nextLocale);
    } catch {
      // A locale choice is an enhancement; keep it in memory when storage is unavailable.
    }
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    root.lang = locale;
    root.dir = locale === "ar" ? "rtl" : "ltr";
    return () => {
      root.lang = "en";
      root.dir = "ltr";
    };
  }, [locale]);

  const value = useMemo<LocaleContextValue>(
    () => ({ locale, setLocale, t: (key, values) => translate(locale, key, values) }),
    [locale, setLocale],
  );
  return <LocaleContext.Provider value={value}>{children}</LocaleContext.Provider>;
}

export function useLocale() {
  return useContext(LocaleContext);
}
