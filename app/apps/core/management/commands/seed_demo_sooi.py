from decimal import Decimal
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.busquedas.models import SearchProfile, SearchRun
from apps.fuentes.models import Source
from apps.inmuebles.models import CapturedProperty
from apps.seguimiento.models import (
    Alert,
    BrokerCompany,
    FollowUpTask,
    OpportunityActivity,
    OpportunityContact,
    PropertyOpportunity,
)


class Command(BaseCommand):
    help = "Crea datos ficticios de demo para SOOI."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Elimina datos previos del usuario demo antes de recrearlos.")
        parser.add_argument("--username", default="demo")
        parser.add_argument("--password", default="DemoSooi2026!")

    def handle(self, *args, **options):
        User = get_user_model()
        username = options["username"]
        password = options["password"]

        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                "email": "demo@sooi.io",
                "first_name": "Usuario",
                "last_name": "Demo",
                "is_active": True,
            },
        )
        user.email = "demo@sooi.io"
        user.first_name = "Usuario"
        user.last_name = "Demo"
        user.is_active = True
        user.set_password(password)
        user.save()

        if options["reset"]:
            self.stdout.write("Limpiando datos anteriores del usuario demo...")
            Alert.objects.filter(owner=user).delete()
            FollowUpTask.objects.filter(owner=user).delete()
            OpportunityActivity.objects.filter(created_by=user).delete()
            PropertyOpportunity.objects.filter(owner=user).delete()
            CapturedProperty.objects.filter(owner=user).delete()
            SearchRun.objects.filter(search_profile__owner=user).delete()
            SearchProfile.objects.filter(owner=user).delete()
            BrokerCompany.objects.filter(owner=user).delete()
            OpportunityContact.objects.filter(owner=user).delete()

        idealista, _ = Source.objects.get_or_create(
            code="idealista",
            defaults={
                "name": "Idealista",
                "base_url": "https://www.idealista.com",
                "source_type": Source.SourceType.PORTAL,
                "is_active": True,
                "is_verified": True,
            },
        )
        servihabitat, _ = Source.objects.get_or_create(
            code="servihabitat",
            defaults={
                "name": "Servihabitat",
                "base_url": "https://www.servihabitat.com",
                "source_type": Source.SourceType.PORTAL,
                "is_active": True,
                "is_verified": True,
            },
        )
        manual, _ = Source.objects.get_or_create(
            code="manual",
            defaults={
                "name": "Entrada manual",
                "base_url": "",
                "source_type": Source.SourceType.MANUAL,
                "is_active": True,
                "is_verified": True,
            },
        )

        now = timezone.now()

        searches_data = [
            {
                "name": "Córdoba · casas con margen de negociación",
                "operation_type": SearchProfile.OperationType.SALE,
                "province": "Córdoba",
                "zone": "Pozoblanco",
                "property_types": [SearchProfile.PropertyType.HOUSE],
                "min_price": Decimal("35000"),
                "max_price": Decimal("95000"),
                "min_area_m2": Decimal("90"),
                "min_bedrooms": 2,
                "color": SearchProfile.Color.BLUE,
                "ai_prompt": "Priorizar viviendas con necesidad de reforma, descuento potencial y señales de urgencia.",
            },
            {
                "name": "Málaga interior · alquiler económico",
                "operation_type": SearchProfile.OperationType.RENT,
                "province": "Málaga",
                "zone": "Cártama / Campanillas",
                "property_types": [SearchProfile.PropertyType.FLAT, SearchProfile.PropertyType.HOUSE],
                "min_price": Decimal("400"),
                "max_price": Decimal("850"),
                "min_area_m2": Decimal("65"),
                "min_bedrooms": 2,
                "color": SearchProfile.Color.GREEN,
                "ai_prompt": "Buscar alquiler estable de larga duración con buena conexión a Málaga.",
            },
            {
                "name": "Locales pequeños con rentabilidad",
                "operation_type": SearchProfile.OperationType.SALE,
                "province": "Córdoba",
                "zone": "Zona norte",
                "property_types": [SearchProfile.PropertyType.COMMERCIAL],
                "min_price": Decimal("20000"),
                "max_price": Decimal("70000"),
                "min_area_m2": Decimal("40"),
                "min_bedrooms": None,
                "color": SearchProfile.Color.ORANGE,
                "ai_prompt": "Locales sencillos, baja inversión inicial y posible alquiler a autónomos.",
            },
        ]

        searches = []
        for data in searches_data:
            sp = SearchProfile.objects.create(
                owner=user,
                status=SearchProfile.Status.ACTIVE,
                is_active=True,
                automation_enabled=False,
                notes="Expediente demo creado para presentación comercial.",
                **data,
            )
            searches.append(sp)

            SearchRun.objects.create(
                search_profile=sp,
                status=SearchRun.Status.COMPLETED,
                execution_mode=SearchRun.ExecutionMode.AI_DISCOVERY,
                provider="demo",
                model_name="demo-data",
                filters_snapshot={
                    "province": sp.province,
                    "zone": sp.zone,
                    "property_types": sp.property_types,
                },
                warnings=[
                    "Datos ficticios de demostración. No corresponden a inmuebles reales."
                ],
                started_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1, minutes=55),
                total_candidates=8,
                total_valid_candidates=5,
                total_found=5,
                total_new=5,
                total_updated=0,
                total_errors=0,
                run_notes="Ejecución demo simulada.",
            )

        capture_data = [
            {
                "search": searches[0],
                "source": idealista,
                "title": "Casa reformable con patio en Pozoblanco",
                "municipality": "Pozoblanco",
                "property_type": CapturedProperty.PropertyType.HOUSE,
                "operation_type": CapturedProperty.OperationType.SALE,
                "price": Decimal("58500"),
                "bedrooms": 3,
                "bathrooms": 1,
                "area_m2": Decimal("118"),
                "status": CapturedProperty.Status.VALIDATED,
                "review_status": CapturedProperty.ReviewStatus.REVIEWED,
                "is_interesting": True,
                "possible_duplicate": False,
                "ai_score": Decimal("82.00"),
                "signals": ["precio bajo zona", "reforma ligera", "margen negociación"],
            },
            {
                "search": searches[0],
                "source": idealista,
                "title": "Vivienda urbana con posible descuento",
                "municipality": "Pozoblanco",
                "property_type": CapturedProperty.PropertyType.HOUSE,
                "operation_type": CapturedProperty.OperationType.SALE,
                "price": Decimal("74000"),
                "bedrooms": 4,
                "bathrooms": 2,
                "area_m2": Decimal("142"),
                "status": CapturedProperty.Status.IN_REVIEW,
                "review_status": CapturedProperty.ReviewStatus.PENDING,
                "is_interesting": True,
                "possible_duplicate": True,
                "ai_score": Decimal("68.00"),
                "signals": ["posible duplicado", "superficie atractiva"],
            },
            {
                "search": searches[1],
                "source": manual,
                "title": "Piso amplio en alquiler cerca de Campanillas",
                "municipality": "Campanillas",
                "property_type": CapturedProperty.PropertyType.FLAT,
                "operation_type": CapturedProperty.OperationType.RENT,
                "price": Decimal("720"),
                "bedrooms": 3,
                "bathrooms": 1,
                "area_m2": Decimal("82"),
                "status": CapturedProperty.Status.VALIDATED,
                "review_status": CapturedProperty.ReviewStatus.REVIEWED_WITH_CHANGES,
                "is_interesting": True,
                "possible_duplicate": False,
                "ai_score": Decimal("76.00"),
                "signals": ["larga duración", "precio dentro de rango"],
            },
            {
                "search": searches[2],
                "source": servihabitat,
                "title": "Local pequeño para alquiler operativo",
                "municipality": "Peñarroya-Pueblonuevo",
                "property_type": CapturedProperty.PropertyType.COMMERCIAL,
                "operation_type": CapturedProperty.OperationType.SALE,
                "price": Decimal("39000"),
                "bedrooms": None,
                "bathrooms": 1,
                "area_m2": Decimal("52"),
                "status": CapturedProperty.Status.CAPTURED,
                "review_status": CapturedProperty.ReviewStatus.PENDING,
                "is_interesting": False,
                "possible_duplicate": False,
                "ai_score": Decimal("61.00"),
                "signals": ["ticket bajo", "revisar zona"],
            },
            {
                "search": searches[0],
                "source": idealista,
                "title": "Casa repetida detectada por similitud",
                "municipality": "Pozoblanco",
                "property_type": CapturedProperty.PropertyType.HOUSE,
                "operation_type": CapturedProperty.OperationType.SALE,
                "price": Decimal("59000"),
                "bedrooms": 3,
                "bathrooms": 1,
                "area_m2": Decimal("116"),
                "status": CapturedProperty.Status.CAPTURED,
                "review_status": CapturedProperty.ReviewStatus.PENDING,
                "is_interesting": False,
                "possible_duplicate": True,
                "ai_score": Decimal("48.00"),
                "signals": ["posible duplicado", "confirmar antes de validar"],
            },
        ]

        captures = []
        for idx, data in enumerate(capture_data, start=1):
            sp = data.pop("search")
            source = data.pop("source")
            signals = data.pop("signals")
            cp = CapturedProperty.objects.create(
                owner=user,
                search_profile=sp,
                search_run=sp.runs.first(),
                source=source,
                entry_mode=CapturedProperty.EntryMode.MANUAL if source.code == "manual" else CapturedProperty.EntryMode.AI_EXPLORATION,
                province=sp.province,
                zone_text=sp.zone,
                source_external_id=f"demo-{idx}",
                source_url=f"https://sooi.io/demo/inmueble-{idx}",
                description_raw="Ficha ficticia de demostración para enseñar el flujo SOOI.",
                ai_summary="Resumen demo: inmueble con señales operativas para revisión y seguimiento.",
                ai_signals=signals,
                manual_notes="Dato ficticio. Usar solo para demostración.",
                last_seen_at=now - timedelta(hours=idx),
                **data,
            )
            captures.append(cp)

        broker = BrokerCompany.objects.create(
            owner=user,
            name="Demo Gestión Inmobiliaria",
            website="https://sooi.io",
            phone="+34 600 000 000",
            email="demo@sooi.io",
            notes="Comercializadora ficticia para demo.",
        )

        contact = OpportunityContact.objects.create(
            owner=user,
            full_name="Laura Demo",
            role=OpportunityContact.Role.AGENT,
            phone="+34 611 111 111",
            email="laura.demo@sooi.io",
            preferred_channel=OpportunityContact.PreferredChannel.WHATSAPP,
            notes="Contacto ficticio de oportunidad.",
        )

        opp1 = PropertyOpportunity.objects.create(
            owner=user,
            captured_property=captures[0],
            search_profile=searches[0],
            title="Casa reformable con patio en Pozoblanco",
            status=PropertyOpportunity.Status.ANALYSIS,
            priority=PropertyOpportunity.Priority.HIGH,
            assigned_to=user,
            broker_company=broker,
            main_contact=contact,
            next_action_type=PropertyOpportunity.NextActionType.CALL,
            next_action_notes="Llamar para confirmar margen de negociación y estado real.",
            next_review_at=now + timedelta(days=1),
            opportunity_score=82,
            province="Córdoba",
            municipality="Pozoblanco",
            zone="Centro",
            asking_price_current=Decimal("58500"),
            target_price_internal=Decimal("52000"),
            max_offer_price=Decimal("50000"),
            expected_rent_monthly=Decimal("480"),
            summary="Oportunidad demo con precio ajustado y posible rentabilidad tras reforma ligera.",
            decision_notes="Solicitar más fotos, confirmar cargas y visitar antes de oferta.",
            last_activity_at=now - timedelta(hours=3),
        )

        opp2 = PropertyOpportunity.objects.create(
            owner=user,
            captured_property=captures[2],
            search_profile=searches[1],
            title="Piso amplio en alquiler cerca de Campanillas",
            status=PropertyOpportunity.Status.ACTIVE,
            priority=PropertyOpportunity.Priority.MEDIUM,
            assigned_to=user,
            main_contact=contact,
            next_action_type=PropertyOpportunity.NextActionType.SCHEDULE_VISIT,
            next_action_notes="Agendar visita de comprobación.",
            next_review_at=now + timedelta(days=2),
            opportunity_score=76,
            province="Málaga",
            municipality="Campanillas",
            zone="Área metropolitana",
            asking_price_current=Decimal("720"),
            summary="Oportunidad demo orientada a necesidad residencial y comparación de alquileres.",
            decision_notes="Validar duración contractual y condiciones de entrada.",
            last_activity_at=now - timedelta(hours=6),
        )

        OpportunityActivity.objects.create(
            opportunity=opp1,
            activity_type=OpportunityActivity.ActivityType.NOTE,
            summary="Primera revisión demo",
            details="Se detecta posible margen de negociación. Pendiente llamada.",
            created_by=user,
        )
        OpportunityActivity.objects.create(
            opportunity=opp2,
            activity_type=OpportunityActivity.ActivityType.TASK,
            summary="Preparar visita",
            details="Revisar ubicación exacta y condiciones antes de avanzar.",
            created_by=user,
        )

        FollowUpTask.objects.create(
            owner=user,
            assigned_to=user,
            property_opportunity=opp1,
            captured_property=captures[0],
            task_type=FollowUpTask.TaskType.CONTACT_OWNER,
            title="Contactar agente de la casa reformable",
            description="Confirmar precio, estado, disponibilidad y margen de negociación.",
            status=FollowUpTask.Status.OPEN,
            priority=FollowUpTask.Priority.HIGH,
            due_date=now + timedelta(days=1),
        )

        Alert.objects.create(
            owner=user,
            property_opportunity=opp1,
            captured_property=captures[0],
            alert_type=Alert.AlertType.PRICE_DROP,
            severity=Alert.Severity.HIGH,
            status=Alert.Status.NEW,
            title="Bajada de precio detectada",
            message="La oportunidad demo simula una bajada de precio relevante.",
            description="Alerta ficticia para mostrar valor operativo.",
        )
        Alert.objects.create(
            owner=user,
            captured_property=captures[1],
            alert_type=Alert.AlertType.POSSIBLE_DUPLICATE,
            severity=Alert.Severity.MEDIUM,
            status=Alert.Status.NEW,
            title="Posible duplicado en bandeja",
            message="Revisa si esta captación corresponde al mismo inmueble.",
            description="Alerta ficticia de control de ruido.",
        )

        self.stdout.write(self.style.SUCCESS("Demo SOOI creada correctamente."))
        self.stdout.write(f"Usuario: {username}")
        self.stdout.write(f"Contraseña: {password}")
        self.stdout.write(f"Búsquedas: {SearchProfile.objects.filter(owner=user).count()}")
        self.stdout.write(f"Captaciones: {CapturedProperty.objects.filter(owner=user).count()}")
        self.stdout.write(f"Oportunidades: {PropertyOpportunity.objects.filter(owner=user).count()}")
        self.stdout.write(f"Tareas: {FollowUpTask.objects.filter(owner=user).count()}")
        self.stdout.write(f"Alertas: {Alert.objects.filter(owner=user).count()}")
