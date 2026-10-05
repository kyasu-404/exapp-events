<?php
declare(strict_types=1);

namespace OCA\ExAppEventsBridge\AppInfo;

use OCA\AppAPI\Middleware\AppAPIAuthMiddleware;
use OCP\AppFramework\App;
use OCP\AppFramework\Bootstrap\IBootContext;
use OCP\AppFramework\Bootstrap\IBootstrap;
use OCP\AppFramework\Bootstrap\IRegistrationContext;

class Application extends App implements IBootstrap {
    public const APP_ID = 'exapp_events_bridge';

    public function __construct(array $urlParams = []) {
        parent::__construct(self::APP_ID, $urlParams);
    }

    public function register(IRegistrationContext $context): void {
        // AppAPI's middleware is scoped to its own app unless registered here too.
        $context->registerMiddleware(AppAPIAuthMiddleware::class);
    }

    public function boot(IBootContext $context): void {
    }
}
