-- Миграция: добавить trial_used в users и is_trial в subscriptions
-- Запускать: psql postgresql://vpnbot:...@localhost:5432/vpnbot -f migration.sql

BEGIN;

-- Колонка trial_used в users (если не существует)
ALTER TABLE public.users
    ADD COLUMN IF NOT EXISTS trial_used BOOLEAN NOT NULL DEFAULT FALSE;

-- Колонка is_trial в subscriptions (если не существует)
ALTER TABLE public.subscriptions
    ADD COLUMN IF NOT EXISTS is_trial BOOLEAN NOT NULL DEFAULT FALSE;

-- Помечаем существующие trial-подписки (plan = 'trial') как is_trial = TRUE
UPDATE public.subscriptions
SET is_trial = TRUE
WHERE plan = 'trial';

-- Помечаем пользователей у которых уже была trial-подписка
UPDATE public.users
SET trial_used = TRUE
WHERE id IN (
    SELECT DISTINCT user_id FROM public.subscriptions WHERE is_trial = TRUE
);

COMMIT;

-- Проверка:
-- SELECT id, username, trial_used FROM users WHERE trial_used = TRUE;
-- SELECT id, user_id, plan, is_trial FROM subscriptions WHERE is_trial = TRUE;