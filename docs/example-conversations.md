# Example conversations

Four conversations for the Grupo Sazón delivery-driver offer. The first one was
produced end to end by the real model on the local installation. The other three
are scripted seed data that the walkthrough loads with `make seed`; they show the
outcomes that need a person. Openings, travel times and interview slots come from
the installation's local connectors with seeded data, not from real systems.

## 1. Qualified, interview booked (English, real model)

Lucía Martín is a synthetic candidate. The conversation ran on 04/11/2026 with
GPT-6.1 Terra: conversation at `low`, guardian, signals and evaluator at `medium`
(Jev off). Fourteen conversation-model calls, ten guardian reviews, eight signal
classifications and one evaluation; traced model
cost **0.0549 USD**. It shows a neighbourhood alias (Russafa is written Ruzafa in the
location name), a nearby location with afternoon openings found by a tool, a
relative date turned into a calendar date, a single summary, and a booking that is
only offered once the `qualified` decision has been applied.

> **Sofía**: Hi! I’m Sofía, Grupo Sazón’s virtual AI assistant. What’s your first and last name?
>
> **Lucía**: Lucía Martín
>
> **Sofía**: Thanks, Lucía. Do you have a valid driving licence? Yes or no?
>
> **Lucía**: yes, I've had an A2 for three years
>
> **Sofía**: Thanks! Which city and area are you looking to work in?
>
> **Lucía**: valencia, I live in Russafa
>
> **Sofía**: Are you available full-time, part-time, or on weekends?
>
> **Lucía**: part-time, afternoons ideally
>
> **Sofía**: Sazón Ruzafa has afternoon vacancies and is approximately one minute from Russafa by motorbike. Would that location suit you?
>
> **Lucía**: yes, that's perfect
>
> **Sofía**: Great! Do you have any delivery experience?
>
> **Lucía**: yes, one year with Glovo
>
> **Sofía**: Thanks! When would you be available to start?
>
> **Lucía**: I can start on Monday
>
> **Sofía**: Here’s my summary: Lucía Martín, valid A2 licence, based in Russafa, Valencia; seeking part-time work, preferably afternoons, and happy with Sazón Ruzafa; one year’s delivery experience with Glovo; available to start Monday, 9 November 2026. Is that all correct?
>
> **Lucía**: yes, all correct
>
> **Sofía**: You can book a 15-minute interview at Sazón Ruzafa on Thursday, 5 November at 10:00, 11:00 or 12:00. Which time would you prefer?
>
> **Lucía**: 11:00 please
>
> **Sofía**: Your interview is booked for Thursday, 5 November 2026 at 11:00 (mainland Spain time) at Sazón Ruzafa; it lasts 15 minutes. To change or cancel it, return to this conversation using your personal link and let us know.

Structured result stored by the platform. Field keys come from the client's
configuration (in Spanish); every value carries the candidate's verbatim quote,
which the platform checks against the transcript before accepting it. Reason and
summary are written in the hiring team's language.

```json
{
  "verdict": "qualified",
  "reason": "Cumple los requisitos imprescindibles. Acepta Sazón Ruzafa, con vacantes de tarde y a un minuto en moto, y puede incorporarse dentro del plazo.",
  "fields": {
    "nombre_completo": {
      "value": "Lucía Martín",
      "evidence": "Lucía Martín",
      "confidence": "high"
    },
    "carnet": {
      "value": true,
      "evidence": "yes, I've had an A2 for three years",
      "confidence": "high"
    },
    "tipo_carnet": {
      "value": "A2",
      "evidence": "yes, I've had an A2 for three years",
      "confidence": "high"
    },
    "ciudad": {
      "value": "Valencia",
      "evidence": "valencia, I live in Russafa",
      "confidence": "high"
    },
    "zona": {
      "value": "Russafa",
      "evidence": "valencia, I live in Russafa",
      "confidence": "high"
    },
    "disponibilidad": {
      "value": "media jornada",
      "evidence": "part-time, afternoons ideally",
      "confidence": "high"
    },
    "turno": {
      "value": "tarde",
      "evidence": "part-time, afternoons ideally",
      "confidence": "high"
    },
    "local_asignado": {
      "value": "Sazón Ruzafa",
      "evidence": "yes, that's perfect",
      "confidence": "high"
    },
    "experiencia_anios": {
      "value": 1,
      "evidence": "yes, one year with Glovo",
      "confidence": "high"
    },
    "experiencia_plataforma": {
      "value": "Glovo",
      "evidence": "yes, one year with Glovo",
      "confidence": "high"
    },
    "incorporacion": {
      "value": "2026-11-09",
      "evidence": "I can start on Monday",
      "confidence": "high"
    }
  },
  "flags": [],
  "summary": "Lucía Martín busca media jornada de tarde en Valencia. Tiene carnet A2 vigente, un año de experiencia con Glovo y está disponible desde el 9 de noviembre de 2026."
}
```

The fit score shown to the hiring team is computed by the platform from the offer's
criteria, not by the model. The decision was applied automatically because the
offer's autonomy allows it for `qualified`; rejections never are.

## 2. Missing licence: a rejection proposal, not a rejection (Spanish, seed)

The licence is a hard requirement. The agent says so without closing the door and
the case waits for a person. Rough translation: the candidate has no licence right
now; the assistant explains it is required and that the team will review the case.

> **Sofía**: Hola, soy Sofía, la asistente virtual de Grupo Sazón. Te haré unas preguntas cortas para la oferta de repartidor/a. ¿Cómo te llamas?
>
> **Andrés**: Andrés Gil Navarro
>
> **Sofía**: Gracias, Andrés. ¿Tienes carnet de conducir en vigor?
>
> **Andrés**: No, ahora mismo no tengo carnet
>
> **Sofía**: Entendido. El carnet es imprescindible para esta oferta, así que el equipo de selección revisará tu candidatura y te dirá algo. ¿En qué ciudad vives?
>
> **Andrés**: En València, por Benimaclet
>
> **Sofía**: Gracias por tu tiempo, Andrés. Selección revisará tu caso y te contactará.

```json
{
  "verdict": "not_qualified",
  "fields": {
    "nombre_completo": {
      "value": "Andrés Gil Navarro",
      "evidence": "Andrés Gil Navarro",
      "confidence": "high"
    },
    "carnet": {
      "value": false,
      "evidence": "No, ahora mismo no tengo carnet",
      "confidence": "high"
    },
    "ciudad": {
      "value": "València",
      "evidence": "En València, por Benimaclet",
      "confidence": "high"
    },
    "zona": {
      "value": "Benimaclet",
      "evidence": "En València, por Benimaclet",
      "confidence": "high"
    }
  },
  "summary": "Sin carnet vigente, requisito imprescindible. Propuesta de descarte pendiente de revisión humana."
}
```

In the console this appears as a proposal that a recruiter confirms or overrides,
with a mandatory reason.

## 3. Asks for a person: hand-off (Spanish, seed)

The candidate wants the exact hourly pay before continuing. Pay is not in the
client's sources, so the agent does not invent it; when the candidate insists on a
person, the AI stops and the conversation moves to the inbox, where a recruiter can
take it over and later hand it back.

> **Sofía**: Hola, soy Sofía, la asistente virtual de Grupo Sazón. Te haré unas preguntas cortas para la oferta de repartidor/a. ¿Cómo te llamas?
>
> **Carmen**: Carmen Ibáñez Gómez
>
> **Sofía**: ¿Tienes carnet de conducir en vigor?
>
> **Carmen**: Sí, el B. Pero antes dime cuánto se cobra por hora
>
> **Sofía**: El encargado te detalla el sueldo y los pluses en la entrevista. ¿En qué ciudad vives?
>
> **Carmen**: No sigo sin saber el sueldo exacto, quiero hablar con alguien de verdad
>
> **Sofía**: De acuerdo, te paso con una persona del equipo de selección. Te escribirán por aquí.

## 4. Silence: reminders and abandonment (Spanish, seed)

The candidate stops replying at the availability question. The platform schedules
up to three reminders outside quiet hours and, in a live conversation, the agent
writes each one (the texts below are the seed's samples); any reply would cancel
the rest. After the last one, the application is recorded as abandoned
at the `disponibilidad` stage, which feeds the drop-off analytics.

> **Sofía**: Hola, soy Sofía, la asistente virtual de Grupo Sazón. Te haré unas preguntas cortas para la oferta de repartidor/a. ¿Cómo te llamas?
>
> **Pedro**: Pedro Lozano Martí
>
> **Sofía**: ¿Tienes carnet de conducir en vigor?
>
> **Pedro**: Sí, B
>
> **Sofía**: ¿En qué ciudad y zona vives?
>
> **Pedro**: Castellón, en el Grau
>
> **Sofía**: ¿Qué disponibilidad tienes: completa, media jornada o fines de semana?
>
> **Sofía**: Hola, Pedro. Cuando puedas, dime qué disponibilidad tienes y seguimos.
>
> **Sofía**: Buenos días, Pedro. Cuando puedas, dime tu disponibilidad y el turno que prefieres.
>
> **Sofía**: Último aviso, Pedro: si te sigue interesando, contesta cuando quieras.
