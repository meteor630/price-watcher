import sys

from pricewatch.bootstrap.commands import main
from pricewatch.observability.json_log import use_utf8_streams

use_utf8_streams()
sys.exit(main())
