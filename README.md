# IaC Tool for Yandex Cloud

Учебный проект по дисциплине "Объектно-ориентированное программирование". Инструмент реализует декларативное управление инфраструктурой Yandex Cloud через официальный Python SDK `yandexcloud`.

## Возможности MVP

- `validate` проверяет YAML-манифест и связи между ресурсами.
- `plan` строит план изменений по локальному `state.json`.
- `state` показывает текущее локальное состояние, сохраненное в `state.json`.
- `graph` генерирует граф зависимостей ресурсов в формате Graphviz DOT.
- `drift-detect` сверяет манифест и `state.json` с реальным состоянием в Yandex Cloud.
- `outputs` показывает стандартные live-outputs по управляемым ресурсам.
- `apply --confirm` создает, обновляет, пересоздает и удаляет ресурсы, которые были исключены из манифеста.
- `destroy --confirm` удаляет всю инфраструктуру из `state.json` в обратном порядке зависимостей.
- Поддерживаются ресурсы `network`, `security_group`, `subnet`, `disk`, `instance`.

## Архитектура

Проект построен по цепочке:

`CLI -> Manifest Loader -> Planner -> Executor -> Yandex Cloud Facade -> State Store`

ООП-паттерны, которые демонстрируются в коде:

- `Facade`: класс `YandexCloudFacade` скрывает детали SDK и gRPC-вызовов.
- `Factory`: `ResourceHandlerFactory` создает обработчики ресурсов из манифеста.
- `Command`: операции плана представлены командами `CreateResourceCommand`, `UpdateResourceCommand`, `DeleteResourceCommand` и `DeleteStateResourceCommand`.
- Полиморфизм и абстрактный базовый класс: `CloudResourceHandler` задает общий контракт для разных типов ресурсов.

Планировщик строит порядок операций по графу зависимостей, поэтому ресурсы создаются после своих зависимостей, а удаляются в обратном порядке. Если ресурс присутствует в `state.json`, но отсутствует в текущем манифесте, он попадает в план как `delete`.

Для части изменений инструмент выполняет `update` без пересоздания ресурса. В текущей версии обновляются `name` и `labels` у базовых ресурсов, правила у `security_group`, размер у `disk`, а также `name`, `labels`, `cores`, `memory_gb`, `preemptible` и `security_groups` у `instance`. При изменении `cores` или `memory_gb` VM временно останавливается, обновляется и запускается обратно, если до операции она была запущена. Изменения полей, которые безопаснее заменить целиком, например CIDR подсети, образ VM, пользователь SSH или boot disk, остаются `replace`.

## Структура проекта

```text
.
├── docs/
├── examples/
├── src/iac_tool/
└── tests/
```

## Установка

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Аутентификация

Инструмент не хранит секреты в манифесте. Поддерживаются:

- `YC_IAM_TOKEN`
- `YC_OAUTH_TOKEN`
- `YC_SERVICE_ACCOUNT_KEY_FILE`
- локальный JSON-файл `.iac-tool-auth.json`

Пример локального auth-файла есть в `examples/auth-config.example.json`.

## Пример манифеста

См. `examples/sample-manifest.yaml`.

Перед первым запуском убедитесь, что `provider.folder_id` содержит реальный ID вашей папки в Yandex Cloud.
Манифест использует коллекции `networks`, `security_groups`, `subnets`, `disks`, `instances`, поэтому в одном YAML можно описывать сразу несколько ресурсов каждого типа.

Полезные связи между ресурсами:

- `subnets[].network` ссылается на `networks[].logical_name`
- `security_groups[].network` ссылается на `networks[].logical_name`
- `instances[].subnet` ссылается на `subnets[].logical_name`
- `instances[].security_groups` ссылается на `security_groups[].logical_name`
- `instances[].data_disks` ссылается на `disks[].logical_name`

Для `security_groups` поддерживаются `ingress_rules` и `egress_rules` с полями `protocol`, `cidr_blocks`, а также необязательными `from_port` и `to_port`.
Для `disks` поддерживаются `name`, `size_gb`, `type_id` и `labels`.

## Команды CLI

```bash
iac-tool validate examples/sample-manifest.yaml
iac-tool plan examples/sample-manifest.yaml
iac-tool state examples/sample-manifest.yaml
iac-tool graph examples/sample-manifest.yaml
iac-tool drift-detect examples/sample-manifest.yaml
iac-tool outputs examples/sample-manifest.yaml
iac-tool apply examples/sample-manifest.yaml --confirm
iac-tool destroy examples/sample-manifest.yaml --confirm
```

По умолчанию `state.json` создается рядом с манифестом. При необходимости путь можно переопределить через `--state-file`.

Для машинной обработки состояния можно вывести сырой JSON:

```bash
iac-tool state examples/sample-manifest.yaml --json
```

Для построения графа зависимостей в стиле `terraform graph`:

```bash
iac-tool graph examples/sample-manifest.yaml --output infrastructure.dot
dot -Tpng infrastructure.dot -o infrastructure.png
```

Команда `graph` не обращается к облаку и не требует аутентификации, она работает только по локальному манифесту.

Для проверки drift между манифестом, локальным `state.json` и реальным облаком:

```bash
iac-tool drift-detect examples/sample-manifest.yaml
iac-tool drift-detect examples/sample-manifest.yaml --json
```

Команда `drift-detect` требует аутентификации в Yandex Cloud и возвращает код `2`, если обнаружены расхождения.

Для получения стандартных live-outputs из облака:

```bash
iac-tool outputs examples/sample-manifest.yaml
iac-tool outputs examples/sample-manifest.yaml --json
```

Команда `outputs` читает ресурсы по `resource_id` из `state.json` и обращается к Yandex Cloud за актуальными значениями, например `public_ip`, `fqdn`, `subnet_id` и `attached_instance_ids`.
После успешного `apply` этот же стандартный набор live-outputs печатается автоматически.

Для диагностики ошибок доступны подробные логи:

```bash
iac-tool --verbose plan examples/sample-manifest.yaml
iac-tool --verbose --log-file ./iac-tool.log apply examples/sample-manifest.yaml --confirm
```

`--verbose` печатает подробные шаги в stderr, а `--log-file` сохраняет полный диагностический журнал в файл.

## Тестирование

```bash
pytest
```

Интеграционный тест с реальным облаком запускается отдельно:

```bash
YC_RUN_INTEGRATION=1 pytest -m integration
```

Для него нужно задать:

- `YC_IAM_TOKEN` или другой поддерживаемый способ аутентификации
- `YC_TEST_FOLDER_ID`
- `YC_TEST_ZONE_ID`
- `YC_TEST_SSH_PUBLIC_KEY_PATH`

## Документы

- [Каркас пояснительной записки](docs/explanatory-note-outline.md)
- [Сценарий демонстрации](docs/demo-scenario.md)
- [UML class diagram](docs/uml-diagrams.puml)
- [UML component diagram](docs/component-diagram.puml)
- [UML apply sequence](docs/apply-sequence.puml)

## Актуальные источники

- [Yandex Cloud SDK quickstart, updated March 17, 2026](https://yandex.cloud/en/docs/overview/sdk/quickstart)
- [Yandex Cloud Python SDK repository](https://github.com/yandex-cloud/python-sdk)
- [Yandex Cloud VPC NetworkService.Create gRPC reference](https://yandex.cloud/en/docs/vpc/api-ref/grpc/Network/create)
- [Yandex Cloud VPC SecurityGroupService.Create gRPC reference](https://yandex.cloud/en/docs/vpc/api-ref/grpc/SecurityGroup/create)
- [Yandex Cloud VPC SubnetService.Create gRPC reference](https://yandex.cloud/en/docs/vpc/api-ref/grpc/Subnet/create)
- [Yandex Cloud Compute DiskService.Create gRPC reference](https://yandex.cloud/en/docs/compute/api-ref/grpc/Disk/create)
- [Yandex Cloud Compute InstanceService.Create gRPC reference](https://yandex.cloud/ru/docs/compute/api-ref/grpc/Instance/create)
- [Yandex Cloud Compute ImageService.GetLatestByFamily gRPC reference](https://yandex.cloud/en/docs/compute/api-ref/grpc/Image/getLatestByFamily)
