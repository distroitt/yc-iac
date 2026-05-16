# Сценарий демонстрации

## Подготовка

1. Активировать виртуальное окружение.
2. Убедиться, что задан `YC_IAM_TOKEN` или доступен `.iac-tool-auth.json`.
3. Проверить значения `folder_id`, `zone_id` и путь к публичному SSH-ключу в манифесте.

## Демонстрация

1. Показать содержимое `examples/sample-manifest.yaml`.
2. Выполнить валидацию:

```bash
iac-tool validate examples/sample-manifest.yaml
```

3. Построить план:

```bash
iac-tool plan examples/sample-manifest.yaml
```

4. Применить инфраструктуру:

```bash
iac-tool apply examples/sample-manifest.yaml --confirm
```

5. Показать созданные ресурсы в Yandex Cloud.
6. Повторно запустить `plan` и показать отсутствие изменений.
7. Удалить инфраструктуру:

```bash
iac-tool destroy examples/sample-manifest.yaml --confirm
```

8. Показать, что `state.json` очищен.

