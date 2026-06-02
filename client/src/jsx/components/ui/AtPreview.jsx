import React from 'react';
import classNames from "classnames";
import $ from "jquery";
import Timer from '../../utils/Timer.jsx'

import '../../../styles/ui/at-work-preview.scss'

export default class AtPreview extends React.Component {
	state = {
		active : false
	}
	active = false

	timer = null

	container = React.createRef()
	barContainer = React.createRef()

	currentObj = null

	constructor(props) {
		super(props);
		
		this.timer = new Timer(1, 100);
		this.timer.finished = this.workRollOver;

		$(window).resize(this.onResize);
	}
	onResize = (e) => {
		$(this.container.current).css({ width : $(window).width() + 'px'});
	}

	componentDidMount() {
		this.onResize();
	}

	componentWillUnmount() {
		$(window).off('resize', this.onResize);
	}

	componentDidUpdate() {
		if (this.currentObj) {
			$(this.container.current).find(".e__results-image__preview-input").val(this.currentObj.getDesc());
		}
	}

	showDesc = (_work, _firstTime) => {
		if (this.active) return false;
		this.active = true;
		this.currentObj = _work;
		
		let thumbContainer = _work.container.current;
		
		let posA = {
			transition : "none",
			top : Math.trunc(thumbContainer.offsetTop - $(window).scrollTop() + thumbContainer.getBoundingClientRect().height*0.5) + 'px',
			left : Math.trunc(thumbContainer.offsetLeft - $(window).scrollLeft() + thumbContainer.getBoundingClientRect().width*0.5) + 'px'
		}
		$(this.container.current).css(posA);
	
		 
		this.timer.reset();
		this.timer.start();

		return false;
	}
	hideDesc = () => {
		let thumbContainer = this.currentObj.container.current;
		let posA = {
			top : (thumbContainer.offsetTop - $(window).scrollTop() + thumbContainer.getBoundingClientRect().height*0.5) + 'px',
			left : (thumbContainer.offsetLeft - $(window).scrollLeft() + thumbContainer.getBoundingClientRect().width*0.5) + 'px'
		}
		$(this.container.current).css(posA);

		this.active = false;
		this.timer.reset();
		this.timer.start();

		return false;
	}

	saveDesc = () => {
		let newDesc = $(this.container.current).find(".e__results-image__preview-input").val();
		if (this.currentObj) { this.currentObj.editDesc(newDesc); }
		
		this.hideDesc();
		return false;
	}

	workRollOver = (_timer) => {
		this.setState({ active: this.active });
		if (this.active) {

			$(this.container.current).css({
				transition : "",
				left : "",
				top : ""
			});
			
		} else {

			let thumbContainer = this.currentObj.container.current;
			let posA = {
				transition : "",
				top : (thumbContainer.offsetTop - $(window).scrollTop() + thumbContainer.getBoundingClientRect().height*0.5) + 'px',
				left : (thumbContainer.offsetLeft - $(window).scrollLeft() + thumbContainer.getBoundingClientRect().width*0.5) + 'px'
			}
			$(this.container.current).css(posA);


			if (this.props.hideW) { this.props.hideW();}

			this.currentObj = null;
		}
	}


	
	render() {
		const classnames = classNames({
			"e__results-image__preview" : true,
			"e__results-image__preview--active" : this.state.active
		})
		return (
			<div className={classnames} ref={this.container} >
				{
					<div className="e__results-image__preview-holder" >
						<textarea className="e__results-image__preview-input"></textarea>
						<div className="e__results-image__preview-top" ref={this.barContainer}>
							<button className="e__button secondary"  onClick={this.hideDesc} ><div className="e__icon-back"></div></button>
							<div className="e__results-image__title">Edit Description</div>
							<button className="e__button" onClick={this.saveDesc} >Save</button>
						</div>
					</div>
				}
			</div>
		)
	}
}
