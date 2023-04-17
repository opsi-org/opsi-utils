# -*- coding: utf-8 -*-

# Copyright (c) uib GmbH <info@uib.de>
# License: AGPL-3.0
"""
Creating opsi product source folders.
"""

import argparse
import codecs
import gettext
import os
from pathlib import Path
import sys
import shutil
import time

from opsicommon.logging import logger, logging_config, LOG_ERROR, DEFAULT_COLORED_FORMAT
from opsicommon.package import OpsiPackage
from opsicommon.objects import (
	ProductDependency, LocalbootProduct, NetbootProduct, UnicodeProductProperty, BoolProductProperty
)
from opsicommon.server.rights import set_rights
from opsicommon.types import forceEmailAddress, forceFilename, forceUnicode
from OPSI import __version__ as python_opsi_version  # type: ignore
from OPSI.System import copy  # type: ignore[import]
from OPSI.Util.File import ChangelogFile  # type: ignore[import]
from OPSI.UI import UIFactory  # type: ignore[import]

from opsiutils import __version__

try:
	sp = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
	if os.path.exists(os.path.join(sp, "site-packages")):
		sp = os.path.join(sp, "site-packages")
	sp = os.path.join(sp, 'opsi-utils_data', 'locale')
	translation = gettext.translation('opsi-utils', sp)
	_ = translation.gettext
except Exception as loc_err:  # pylint: disable=broad-except
	logger.debug("Failed to load locale from %s: %s", sp, loc_err)

	def _(string):
		""" Fallback function """
		return string


class CancelledByUserError(Exception):
	pass


def newprod_main():
	parser = argparse.ArgumentParser()
	parser.add_argument('--version', '-V', action='version', version=f"{__version__} [python-opsi={python_opsi_version}]")
	parser.add_argument(
		"-t",
		"--template-dir",
		default=None,
		dest="templateDir",
		metavar="DIRECTORY",
		help=_("Copies the contents of DIRECTORY to the destination directory.")
	)
	parser.add_argument(
		'destination',
		default=os.getcwd(),
		nargs='?',
		help=_("The destination of the new product source. If no destination directory is supplied, the current directory is used."),
	)

	options = parser.parse_args()

	templateDirectory = options.templateDir
	destDir = os.path.abspath(forceFilename(options.destination))

	if not os.path.exists(destDir):
		raise OSError(f"Directory '{destDir}' does not exist!")

	ui = UIFactory(type='snack')
	ui.drawRootText(1, 1, 'opsi')

	try:
		product = getProduct(ui)

		productSourceDir = os.path.join(destDir, product.id)
		if os.path.exists(productSourceDir):
			overwrite = ui.yesno(
				title=_('Overwrite?'),
				text=_('Directory %s already exists, overwrite?') % productSourceDir,
				okLabel=_('Yes'),
				cancelLabel=_('No')
			)

			if not overwrite:
				raise CancelledByUserError(_('Cancelled'))
			shutil.rmtree(productSourceDir)

		os.mkdir(productSourceDir, 0o2770)
		os.mkdir(os.path.join(productSourceDir, 'OPSI'), 0o2770)
		clientDataDirectory = os.path.join(productSourceDir, 'CLIENT_DATA')
		os.mkdir(clientDataDirectory, 0o2770)
		os.mkdir(os.path.join(productSourceDir, 'SERVER_DATA'), 0o2770)

		createActionScripts(product, clientDataDirectory)

		try:
			productDependencies = collectProductDependencies(ui, product)
			productProperties = collectProductProperties(ui, product)
			writeMaintainerInfo(ui, productSourceDir, product, productDependencies, productProperties)

			createTemplates(productSourceDir, templateDirectory)
			try:
				set_rights(productSourceDir)
			except Exception as err:  # pylint: disable=broad-except
				logger.warning("Failed to set rights: %s", err)

			ui.showMessage(title=_('Done'), text=_("Package source directory '%s' created") % productSourceDir, width=70, height=3)
		except Exception as exc:
			errorMessage = _("Product creation failed. Removing directory {0}").format(productSourceDir)

			logger.info(errorMessage)
			ui.showError(title=_('Product creation failed'), text=errorMessage, width=50, height=5)

			shutil.rmtree(productSourceDir)

			raise exc
	finally:
		try:
			ui.exit()
		except Exception:  # pylint: disable=broad-except
			pass


def getProduct(ui):  # pylint: disable=too-many-branches,too-many-statements,invalid-name
	helpText = ''
	values = [
		{"name": "localboot", "selected": True},
		{"name": "netboot"}
	]
	productType = ui.getSelection(
		radio=True,
		title=_('Please select product type'),
		text=helpText,
		entries=values,
		width=30,
		height=14
	)
	if not productType:
		raise CancelledByUserError(_('Cancelled'))
	productType = productType[0]

	product = None
	if productType == 'localboot':
		product = LocalbootProduct(id='newprod', productVersion='1.0', packageVersion='1')
	elif productType == 'netboot':
		product = NetbootProduct(id='newprod', productVersion='1.0', packageVersion='1')
	product.setDefaults()

	helpText = _('''Product id:       A unique identifier for the product.
Product name:     The full name of the product.
Description:      A description (use \\n for line breaks).
Advice:           An additional important advice.
Product version:  Version defined by software producer.
Package version:  Opsi package version of the product.
License required: Is a license required (0|1)?
Priority:         The installation priority class of this product (value between -100 and 100, 0 = neutral).''')
	while True:
		values = [
			{"name": _("Product id"), "value": product.id},
			{"name": _("Product name"), "value": product.name},
			{"name": _("Description"), "value": product.description},
			{"name": _("Advice"), "value": product.advice},
			{"name": _("Product version"), "value": product.productVersion},
			{"name": _("Package version"), "value": product.packageVersion},
			{"name": _("License required"), "value": product.licenseRequired},
			{"name": _("Priority"), "value": product.priority}
		]
		if productType == 'netboot':
			values.append({"name": _("PXE config template"), "value": product.pxeConfigTemplate})

		values = ui.getValues(
			title=_('product information'),
			text=helpText,
			entries=values
		)

		if not values:
			raise CancelledByUserError(_('Cancelled'))

		error = None
		try:
			product.setId(values[0].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('You have to specify a valid product id.')
		try:
			product.setName(values[1].get('value'))
			if not product.name:
				raise ValueError('No product name specified')
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('You have to specify a valid product name.')
		try:
			product.setDescription(values[2].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Description is not valid.')
		try:
			product.setAdvice(values[3].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Advice is not valid.')
		try:
			product.setProductVersion(values[4].get('value'))
			if not product.productVersion:
				raise ValueError('No product version specified')
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('You have to specify a valid product version.')
		try:
			product.setPackageVersion(values[5].get('value'))
			if not product.packageVersion:
				raise ValueError('No package version specified')
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('You have to specify a valid package version.')
		try:
			product.setLicenseRequired(values[6].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('License required must be a boolean value.')
		try:
			product.setPriority(values[7].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Priority has to be an number between -100 and 100')

		if error:
			ui.showError(title=_('Bad value'), text=error, width=50, height=5)
			continue
		break

	helpText = _('''\
Setup script:        Relative path to script for action "setup".
Uninstall script:    Relative path to script for action "uninstall".
Update script:       Relative path to script for action "update".
Always script:       Relative path to script for action "always".
Once script:         Relative path to script for action "once".
Custom script:       Relative path to script for action "custom".
User login script:   Relative path to script for user login.
PXE config template: path to a custom pxelinux config template.''')
	while True:
		values = [
			{"name": _("Setup script"), "value": product.setupScript},
			{"name": _("Uninstall script"), "value": product.uninstallScript},
			{"name": _("Update script"), "value": product.updateScript},
			{"name": _("Always script"), "value": product.alwaysScript},
			{"name": _("Once script"), "value": product.onceScript},
			{"name": _("Custom script"), "value": product.customScript}
		]

		if productType == 'netboot':
			values.append({"name": _("PXE config template"), "value": product.pxeConfigTemplate})
		else:
			values.append({"name": _("User login script"), "value": product.userLoginScript})

		values = ui.getValues(
			title=_('product scripts'),
			text=helpText,
			entries=values
		)

		if not values:
			raise CancelledByUserError(_('Cancelled'))

		error = None
		try:
			product.setSetupScript(values[0].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Setup script is not valid.')
		try:
			product.setUninstallScript(values[1].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Uninstall script is not valid.')
		try:
			product.setUpdateScript(values[2].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Update script is not valid.')
		try:
			product.setAlwaysScript(values[3].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Always script is not valid.')
		try:
			product.setOnceScript(values[4].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Once script is not valid.')
		try:
			product.setCustomScript(values[5].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Custom script is not valid.')
		if productType == 'netboot':
			try:
				product.setPxeConfigTemplate(values[6].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('PXE config template is not valid.')
		else:
			try:
				product.setUserLoginScript(values[6].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('User login script is not valid.')

		if error:
			ui.showError(title=_('Bad value'), text=error, width=50, height=5)
			continue

		break

	return product


def createActionScripts(product, clientDataDirectory):
	"""
	Create a file for all the scripts set at `product` in `clientDataDirectory`.

	:param product: The product for which the scripts should be created.
	:type product: OPSI.Object.Product
	:param clientDataDirectory: The path in which the scripts should be \
created. Usually the `CLIENT_DATA` directory of a product.
	:type clientDataDirectory: str
	"""
	scriptAttributes = [
		'setupScript', 'uninstallScript', 'updateScript',
		'alwaysScript', 'onceScript', 'customScript',
		'userLoginScript'
	]

	for attribute in scriptAttributes:
		script = getattr(product, attribute, None)
		if not script:
			logger.debug("No %s set, skipping.", attribute)
			continue

		scriptPath = os.path.join(clientDataDirectory, script)
		with codecs.open(scriptPath, 'a', 'utf-8'):
			# Opening the file in append mode to not destroy anything
			# they may be already existing.
			# Remember that multiple actions may refer the same script.
			pass
		logger.info('Created script %s.', scriptPath)


def collectProductDependencies(ui, product):  # pylint: disable=too-many-branches,invalid-name
	productDependencies = []
	helpText = _('''You have to specify a required product id.
You have to specify either a required installation status or a required action.
The requirement type can be used to specify the position of a requirement. This is optional.
Possible actions are: %s
Possible installation status are: %s
Possible requirement types are: %s''') % (
		', '.join(['setup']),
		', '.join(['installed']),
		', '.join(['before', 'after'])
	)

	while True:
		if not ui.yesno(
			title=_('Create product dependency?'),
			text=_('Do you want to create a product dependency?'),
			okLabel=_('Yes'),
			cancelLabel=_('No'),
		):
			break

		productDependency = ProductDependency(
			productId=product.id,
			productVersion=product.productVersion,
			packageVersion=product.packageVersion,
			productAction='setup',
			requiredProductId='product'
		)
		productDependency.setDefaults()
		productDependency.productAction = ''
		productDependency.requiredProductId = ''
		while True:
			values = [
				{"name": _("Dependency for action"), "value": productDependency.productAction},
				{"name": _("Required product id"), "value": productDependency.requiredProductId},
				{"name": _("Required action"), "value": productDependency.requiredAction or ''},
				{"name": _("Required installation status"), "value": productDependency.requiredInstallationStatus or ''},
				{"name": _("Requirement type"), "value": productDependency.requirementType or ''}
			]

			values = ui.getValues(
				title=_('Create dependency for product %s') % product.id,
				text=helpText,
				entries=values
			)

			if not values:
				break

			error = None
			try:
				productDependency.setProductAction(values[0].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('You have to specify a valid product action.')

			try:
				productDependency.setRequiredProductId(values[1].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('You have to specify a valid required product id.')

			if values[2].get('value'):
				try:
					productDependency.setRequiredAction(values[2].get('value'))
				except Exception:  # pylint: disable=broad-except
					if not error:
						error = _('Required action is not valid.')
			elif values[3].get('value'):
				try:
					productDependency.setRequiredInstallationStatus(values[3].get('value'))
				except Exception:  # pylint: disable=broad-except
					if not error:
						error = _('Required installation status is not valid.')
			else:
				if not error:
					error = _('Please specify either a required installation status or a required action.')

			try:
				productDependency.setRequirementType(values[4].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('Requirement type is not valid.')

			if error:
				ui.showError(title=_('Bad value'), text=error, width=50, height=5)
				continue

			productDependencies.append(productDependency)
			break

	return productDependencies


def collectProductProperties(ui, product):  # pylint: disable=too-many-branches,too-many-statements,invalid-name
	productProperties = []
	helpText = _('''Property name: Name of the property.
Property description: Usage description.
Possible values: Comma separated list of possible values for the property. If no possible values are given any values are allowed.
Editable: Is it allowed to specify a value which is not in the list of possible values?''')
	while True:
		if not ui.yesno(
			title=_('Create product property?'),
			text=_('Do you want to create a product property?'),
			okLabel=_('Yes'),
			cancelLabel=_('No')
		):
			break

		# Get property type
		values = [
			{"name": "unicode", "selected": True},
			{"name": "boolean"}
		]
		propertyType = ui.getSelection(
			radio=True,
			title=_('Please select property type'),
			entries=values
		)
		if not propertyType:
			continue
		propertyType = propertyType[0]

		productProperty = None
		if propertyType == 'unicode':
			productProperty = UnicodeProductProperty(
				productId=product.id, productVersion=product.productVersion, packageVersion=product.packageVersion, propertyId='property'
			)
		elif propertyType == 'boolean':
			productProperty = BoolProductProperty(
				productId=product.id, productVersion=product.productVersion, packageVersion=product.packageVersion, propertyId='property'
			)
		productProperty.setDefaults()
		productProperty.propertyId = ''

		while True:
			values = [
				{"name": _("Property name (identifier)"), "value": productProperty.propertyId},
				{"name": _("Property description"), "value": productProperty.description}
			]
			if propertyType == 'unicode':
				values.append({"name": _("Possible values"), "value": ""})
				values.append({"name": _("Editable"), "value": productProperty.editable})

			values = ui.getValues(
				title=_('Create property for product %s') % productProperty.productId,
				text=helpText,
				entries=values
			)

			if not values:
				break

			error = None
			try:
				productProperty.setPropertyId(values[0].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('Please specify a valid identifier')

			try:
				productProperty.setDescription(values[1].get('value'))
			except Exception:  # pylint: disable=broad-except
				if not error:
					error = _('Please specify a valid description')

			if propertyType == 'unicode':
				productProperty.setEditable(values[3].get('value'))
				possibleValues = []
				for val in values[2].get('value').split(','):
					val = val.strip()
					if val != '':
						possibleValues.append(val)
				if possibleValues:
					try:
						productProperty.setPossibleValues(possibleValues)
					except Exception:  # pylint: disable=broad-except
						if not error:
							error = _('Please specify valid possible values')
				else:
					productProperty.possibleValues = []
			if error:
				ui.showError(title=_('Bad value'), text=error, width=50, height=5)
				continue

			try:
				defaultValues = []
				if productProperty.possibleValues:
					if len(productProperty.possibleValues) == 1:
						defaultValues = productProperty.possibleValues
					else:
						choices = []
						for val in productProperty.possibleValues:
							choices.append({"name": val})
						choices[0]['selected'] = True
						result = ui.getSelection(
							title=_('Please select a default value'),
							text="",
							radio=True,
							entries=choices
						)

						if result is not None:
							defaultValues = result
				else:
					result = ui.getValue(
						title=_('Please set a default value'),
						text=""
					)
					if result is not None:
						defaultValues = [result]

				productProperty.setDefaultValues(defaultValues)
			except Exception as err:  # pylint: disable=broad-except
				if not error:
					error = _('Please specify valid default values: %s') % err

			if error:
				ui.showError(title=_('Bad value'), text=error, width=50, height=5)
				continue

			productProperties.append(productProperty)
			break

	return productProperties


def writeMaintainerInfo(ui, productDirectory, product, productDependencies, productProperties):  # pylint: disable=invalid-name
	maintainer = ''
	maintainerEmail = ''
	helpText = _('Maintainer of this opsi package.')
	while True:
		values = [
			{"name": _("Maintainer name"), "value": maintainer},
			{"name": _("Maintainer e-mail"), "value": maintainerEmail}
		]

		values = ui.getValues(
			title=_('Maintainer info'),
			width=70,
			height=10,
			text=helpText,
			entries=values
		)

		if not values:
			raise CancelledByUserError(_('Cancelled'))

		error = None
		try:
			if not values[0].get('value'):
				raise ValueError('Empty maintainer')
			maintainer = forceUnicode(values[0].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Please enter a valid maintainer name.')

		try:
			if not values[1].get('value'):
				raise ValueError('Empty maintainer e-mail')
			maintainerEmail = forceEmailAddress(values[1].get('value'))
		except Exception:  # pylint: disable=broad-except
			if not error:
				error = _('Please enter a valid e-mail address.')

		if error:
			ui.showError(title=_('Bad value'), text=error, width=50, height=5)
			continue

		break

	tmpChangelog = os.path.join(productDirectory, 'OPSI', 'changelog.txt')
	cf = ChangelogFile(tmpChangelog)
	cf.setEntries([{
		'package': product.id,
		'version': f'{product.productVersion}-{product.packageVersion}',
		'release': 'testing',
		'urgency': 'low',
		'changelog': ['  * Initial package'],
		'maintainerName': maintainer,
		'maintainerEmail': maintainerEmail,
		'date': time.time()
	}])
	cf.generate()
	product.setChangelog(''.join(cf.getLines()))
	os.unlink(tmpChangelog)

	opsi_package = OpsiPackage()
	opsi_package.product_dependencies = productDependencies
	opsi_package.product_properties = productProperties
	opsi_package.product = product
	opsi_package.generate_control_file(Path(productDirectory) / "OPSI" / "control.toml")
	os.chmod(str(Path(productDirectory) / "OPSI" / "control.toml"), 0o600)


def createTemplates(productDirectory, templateDirectory=None):
	"""
	Creates templates at the given ``productDirectory``.

	:param templateDirectory: The content of the directory will be \
copied to ``productDirectory``
	"""
	def getEnvVariableLines():
		yield '# The following environment variables can be used to obtain information about the current installation:\n'
		yield '#   PRODUCT_ID: id of the current product\n'
		yield '#   PRODUCT_TYPE: type of the current product\n'
		yield '#   PRODUCT_VERSION: product version\n'
		yield '#   PACKAGE_VERSION: package version\n'
		yield '#   CLIENT_DATA_DIR: directory where client data will be installed\n'

	# Create preinst template
	preinstFilePath = os.path.join(productDirectory, 'OPSI', 'preinst')
	with codecs.open(preinstFilePath, 'w', 'utf-8') as preinst:
		preinst.write('#!/bin/bash\n')
		preinst.write('#\n')
		preinst.write('# preinst script\n')
		preinst.write('# This script executes before that package will be unpacked from its archive file.\n')
		preinst.write('#\n')
		for line in getEnvVariableLines():
			preinst.write(line)
		preinst.write('#\n')

	# Create postinst template
	postinstFilePath = os.path.join(productDirectory, 'OPSI', 'postinst')
	with codecs.open(postinstFilePath, 'w', 'utf-8') as postinst:
		postinst.write('#!/bin/bash\n')
		postinst.write('#\n')
		postinst.write('# postinst script\n')
		postinst.write('# This script executes after unpacking files from that archive and registering the product at the depot.\n')
		postinst.write('#\n')
		for line in getEnvVariableLines():
			postinst.write(line)
		postinst.write('#\n')

	if templateDirectory:
		copy(os.path.join(templateDirectory, '*'), productDirectory)


def main():
	try:
		newprod_main()
	except Exception as err:  # pylint: disable=broad-except
		logging_config(stderr_level=LOG_ERROR, stderr_format=DEFAULT_COLORED_FORMAT)
		logger.error(err, exc_info=True)
		print(f"ERROR: {err}", file=sys.stderr)
		sys.exit(1)
